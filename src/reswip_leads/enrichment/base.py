"""Base enrichment interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseEnricher(ABC):
    """Abstract base class for all enrichers.

    Enrichers fill missing fields on a Lead using external data sources.
    They must never overwrite existing values unless there is reliable evidence.
    """

    @abstractmethod
    def enrich(self, tva: str, **kwargs: Any) -> Dict[str, Any]:
        """Enrich a company record.

        Returns a dict of field → value pairs to merge into the lead.
        Only non-empty values should be returned.
        """

    def merge_if_empty(self, target: Dict[str, str], key: str, value: str) -> bool:
        """Set ``target[key]`` to ``value`` only if it is currently empty.

        Returns True if a value was set.
        """
        if not value or target.get(key):
            return False
        target[key] = value.strip()
        return True
