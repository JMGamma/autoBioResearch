from __future__ import annotations

from unittest.mock import MagicMock

import requests

from autobioresearch.seeders.reactome_fetcher import (
    ReactomeInteractionFetcher,
    _REACTOME_URLS,
)


def _mock_response(status_code: int = 200, url: str = "https://example.test/file.txt") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.url = url
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    return resp


def test_open_stream_falls_back_to_next_url():
    fetcher = ReactomeInteractionFetcher(requests_per_second=100.0, timeout=5, max_retries=1)

    err = requests.HTTPError("404 Client Error")
    first = _mock_response(status_code=404, url=_REACTOME_URLS[1])
    first.raise_for_status.side_effect = err
    second = _mock_response(status_code=200, url=_REACTOME_URLS[0])

    fetcher._session.get = MagicMock(side_effect=[first, second])

    resp = fetcher._open_stream()

    assert resp is second
    assert fetcher._session.get.call_count == 2
    assert fetcher._session.get.call_args_list[0].args[0] == _REACTOME_URLS[0]
    assert fetcher._session.get.call_args_list[1].args[0] == _REACTOME_URLS[1]


def test_parse_line_handles_reactome_psimitab_row():
    fetcher = ReactomeInteractionFetcher(requests_per_second=100.0, timeout=5, max_retries=1)
    line = (
        "uniprotkb:Q9Y287\tuniprotkb:P37840\taltA\taltB\taliasA\taliasB\t"
        "psi-mi:\"MI:0364\"(inferred by curator)\tauthor\tpubmed:14690516|pubmed:10391242\t"
        "taxid:9606(Homo sapiens)\ttaxid:9606(Homo sapiens)\t"
        "psi-mi:\"MI:0915\"(physical association)\tpsi-mi:\"MI:0467\"(reactome)\t"
        "reactome:R-HSA-1247852\treactome-score:0.5\t-\troleA\troleB\texpA\texpB\t"
        "typeA\ttypeB\txrefA\txrefB\tixref\tannA\tannB\tpathway:R-HSA-977225\t"
        "host\tparams\tcreated\tupdated\tchecksumA\tchecksumB\tichecksum\tfalse\t"
        "featureA\tfeatureB\t0\t0\tidentA\tidentB"
    )

    result = fetcher._parse_line(line)

    assert result is not None
    assert result.acc_a == "Q9Y287"
    assert result.acc_b == "P37840"
    assert result.mi_term == "MI:0915"
    assert result.pathway_id == "R-HSA-1247852"
    assert result.interaction_type == "proximal_association"
    assert result.publication_ids == ["14690516", "10391242"]
