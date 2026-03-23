"""
Score-driven query planning and targeted search generation.
"""
from __future__ import annotations

from autobioresearch.config import AppConfig
from autobioresearch.models import QueryType, SearchQuery
from autobioresearch.storage.repositories import Repositories


class QueryPlanner:
    def plan_pending_queries(self, repos: Repositories, config: AppConfig) -> int:
        candidates = repos.queries.get_pending_candidates(limit=max(100, config.queries_per_cycle * 5))
        updated = 0
        for row in candidates:
            score, factors = self.estimate_score_gain(row, repos, config)
            repos.queries.update_planning(
                row["id"],
                planning_score=score,
                planning_factors=factors,
            )
            updated += 1
        return updated

    def estimate_score_gain(self, query_row: dict, repos: Repositories, config: AppConfig) -> tuple[float, dict]:
        query_type = query_row.get("query_type")
        target_kind = query_row.get("target_kind")
        generation_reason = query_row.get("generation_reason")
        planning_factors: dict[str, float | str] = {}

        interaction_gain = 0.0
        conflict_reduction = 0.0
        noise_penalty = 0.0

        if query_type == QueryType.CONFLICT_RESOLUTION.value:
            interaction_gain = 1.0
            conflict_reduction = 2.0
            if (query_row.get("origin") or "").startswith("conflict_id:"):
                conflict_id = query_row["origin"].split(":", 1)[1]
                conflict = next((c for c in repos.conflicts.get_open(limit=200) if c["id"] == conflict_id), None)
                if conflict:
                    conflict_reduction += float(conflict.get("penalty_weight") or 0.0) * config.penalty
                    planning_factors["conflict_penalty_weight"] = float(conflict.get("penalty_weight") or 0.0)
        elif target_kind == "entity":
            interaction_gain = 1.8
            target_id = query_row.get("target_id")
            if target_id:
                entity = repos.entities.get_by_id(target_id)
                if entity:
                    importance = min(float(entity.get("paper_count") or 0), 10.0) / 10.0
                    interaction_gain += importance
                    planning_factors["entity_paper_count"] = float(entity.get("paper_count") or 0)
            noise_penalty = 0.4
        elif target_kind == "interaction":
            interaction_gain = 2.2
            noise_penalty = 0.2
        else:
            interaction_gain = 1.2
            noise_penalty = 0.8 if query_type == QueryType.ENTITY_EXPANSION.value else 0.5

        prior_signal = (
            float(query_row.get("outcome_new_evidence") or 0)
            + float(query_row.get("outcome_new_interactions") or 0) * 1.5
            + float(query_row.get("papers_new") or 0) * 0.5
        )
        learned_yield = self._learned_yield_bonus(
            repos,
            query_type=query_type,
            generation_reason=generation_reason,
            target_kind=target_kind,
        )
        if learned_yield < 0.5:
            noise_penalty += 0.3

        planning_factors["interaction_gain"] = interaction_gain
        planning_factors["conflict_reduction"] = conflict_reduction
        planning_factors["noise_penalty"] = noise_penalty
        planning_factors["prior_signal"] = prior_signal
        planning_factors["learned_yield"] = learned_yield

        estimated_gain = interaction_gain + conflict_reduction + prior_signal + learned_yield - noise_penalty
        return round(estimated_gain, 4), planning_factors

    def generate_targeted_queries(self, repos: Repositories, config: AppConfig) -> int:
        queries: list[SearchQuery] = []
        slots = max(1, config.targeted_queries_per_cycle)
        conflict_slots, entity_slots, interaction_slots = self._allocate_targeted_slots(repos, slots)

        conflict_hubs = repos.conflicts.get_unresolved_conflict_hubs(limit=conflict_slots)
        for conflict in conflict_hubs:
            interaction_type = conflict.get("interaction_type_a") or conflict.get("interaction_type_b") or "interaction"
            query = SearchQuery(
                query_text=(
                    f'"{conflict["entity_a_name"]}"[Title/Abstract] AND '
                    f'"{conflict["entity_b_name"]}"[Title/Abstract] AND '
                    f'({interaction_type} OR mechanism OR context OR condition)'
                ),
                source_api="pubmed",
                query_type=QueryType.CONFLICT_RESOLUTION,
                origin=f"conflict_id:{conflict['id']}",
                generation_reason="unresolved_conflict_hub",
                target_kind="conflict",
                target_id=conflict["id"],
                planning_score=round(3.0 + float(conflict.get("penalty_weight") or 0.0), 4),
                planning_factors={
                    "conflict_penalty_weight": float(conflict.get("penalty_weight") or 0.0),
                    "conflict_axis": conflict.get("conflict_axis") or "",
                },
            )
            queries.append(query)

        sparse_entities = repos.entities.get_sparse_high_value_entities(
            config.min_interactions_per_entity,
            limit=entity_slots,
        )
        for entity in sparse_entities:
            interaction_count = int(entity.get("interaction_count") or 0)
            query = SearchQuery(
                query_text=(
                    f'"{entity["canonical_name"]}"[Title/Abstract] AND '
                    f'(interaction OR signaling OR regulation OR binding)'
                ),
                source_api="pubmed",
                query_type=QueryType.GAP_FILLING,
                origin=f"entity_id:{entity['id']}",
                generation_reason="sparse_high_value_entity",
                target_kind="entity",
                target_id=entity["id"],
                planning_score=round(2.0 + min(float(entity.get("paper_count") or 0), 10.0) / 10.0 - (interaction_count * 0.25), 4),
                planning_factors={
                    "entity_paper_count": float(entity.get("paper_count") or 0),
                    "entity_interaction_count": interaction_count,
                },
            )
            queries.append(query)

        seeded_interactions = repos.interactions.get_under_supported_seeded_interactions(
            config.under_supported_seed_evidence_max,
            limit=interaction_slots,
        )
        for interaction in seeded_interactions:
            query = SearchQuery(
                query_text=(
                    f'"{interaction["entity_a_name"]}"[Title/Abstract] AND '
                    f'"{interaction["entity_b_name"]}"[Title/Abstract] AND '
                    f'({interaction["interaction_type"]} OR interaction OR mechanism)'
                ),
                source_api="pubmed",
                query_type=QueryType.GAP_FILLING,
                origin=f"interaction_id:{interaction['id']}",
                generation_reason="under_supported_seeded_interaction",
                target_kind="interaction",
                target_id=interaction["id"],
                planning_score=round(2.6 + max(0.0, 1.0 - float(interaction.get("evidence_count") or 0)), 4),
                planning_factors={
                    "interaction_evidence_count": float(interaction.get("evidence_count") or 0),
                    "interaction_confidence_score": float(interaction.get("composite_confidence_score") or 0.0),
                },
            )
            queries.append(query)

        return repos.queries.insert_many(queries)

    def _learned_yield_bonus(
        self,
        repos: Repositories,
        *,
        query_type: str | None,
        generation_reason: str | None,
        target_kind: str | None,
    ) -> float:
        specific = repos.queries.get_yield_stats(
            query_type=query_type,
            generation_reason=generation_reason,
            target_kind=target_kind,
        )
        if specific["n"] >= 2:
            return self._yield_score_from_stats(specific)

        broad = repos.queries.get_yield_stats(
            query_type=query_type,
            target_kind=target_kind,
        )
        if broad["n"] >= 2:
            return self._yield_score_from_stats(broad)
        return 0.0

    def _yield_score_from_stats(self, stats: dict) -> float:
        return round(
            (stats["avg_attributed_new_evidence"] * 0.45)
            + (stats["avg_attributed_new_interactions"] * 0.85)
            + (stats["avg_papers_new"] * 0.15)
            + (stats["improved_rate"] * 1.5),
            4,
        )

    def _allocate_targeted_slots(self, repos: Repositories, total_slots: int) -> tuple[int, int, int]:
        families = [
            ("conflict", repos.queries.get_yield_stats(query_type=QueryType.CONFLICT_RESOLUTION.value, generation_reason="unresolved_conflict_hub", target_kind="conflict")),
            ("entity", repos.queries.get_yield_stats(query_type=QueryType.GAP_FILLING.value, generation_reason="sparse_high_value_entity", target_kind="entity")),
            ("interaction", repos.queries.get_yield_stats(query_type=QueryType.GAP_FILLING.value, generation_reason="under_supported_seeded_interaction", target_kind="interaction")),
        ]

        weights: dict[str, float] = {}
        for name, stats in families:
            learned = self._yield_score_from_stats(stats) if stats["n"] >= 2 else 1.0
            weights[name] = max(0.5, learned)

        total_weight = sum(weights.values())
        raw_allocations = {
            name: max(1, int(round((weight / total_weight) * total_slots)))
            for name, weight in weights.items()
        }

        while sum(raw_allocations.values()) > total_slots:
            largest = max(raw_allocations, key=raw_allocations.get)
            if raw_allocations[largest] > 1:
                raw_allocations[largest] -= 1
            else:
                break
        while sum(raw_allocations.values()) < total_slots:
            best = max(weights, key=weights.get)
            raw_allocations[best] += 1

        return (
            raw_allocations["conflict"],
            raw_allocations["entity"],
            raw_allocations["interaction"],
        )
