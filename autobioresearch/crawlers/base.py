"""Abstract base class for all paper crawlers."""
from __future__ import annotations

from abc import ABC, abstractmethod

from autobioresearch.models import Paper


class BaseCrawler(ABC):
    @abstractmethod
    def search(self, query: str, max_results: int = 50) -> list[Paper]:
        """Search for papers matching the query. Returns Paper objects."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Short identifier for this crawler (e.g. 'pubmed')."""
        ...
