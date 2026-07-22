"""TVA-based lead deduplication.

Deduplicates leads primarily by normalized TVA number, merging missing
fields from duplicate rows while preserving existing values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from reswip_leads.core.models import Lead


# Fields that can be merged from duplicates (only if empty on the primary)
_MERGE_FIELDS = (
    "address",
    "city",
    "postcode",
    "province",
    "region",
    "language",
    "first_name",
    "last_name",
    "position",
    "email",
    "phone",
    "mobile",
    "website",
    "nace_codes",
    "status",
)


@dataclass
class DedupeResult:
    """Result of a deduplication run."""

    leads: List[Lead]
    duplicates: List[str]
    input_count: int
    output_count: int


def _is_branch(primary: Lead, candidate: Lead) -> bool:
    """Determine if *candidate* is a different establishment (branch) of *primary`.

    Two records are considered branches — and therefore kept separately —
    when they share the same TVA but have *different* city or address values.
    """
    if not primary.city and not candidate.city:
        return False
    if primary.city and candidate.city and primary.city.strip().lower() != candidate.city.strip().lower():
        return True
    if primary.address and candidate.address and primary.address.strip().lower() != candidate.address.strip().lower():
        return True
    return False


def _merge_into(primary: Lead, secondary: Lead) -> None:
    """Copy missing fields from *secondary* into *primary*.

    Never overwrites non-empty values.
    """
    for attr in _MERGE_FIELDS:
        existing = getattr(primary, attr, "")
        incoming = getattr(secondary, attr, "")
        if not existing and incoming:
            setattr(primary, attr, incoming)


def deduplicate(leads: List[Lead]) -> DedupeResult:
    """Deduplicate a list of leads by normalized TVA.

    Rules:
    - Leads with blank or invalid TVA are never merged with each other.
    - Leads with the same TVA but different city/address are branches and kept separate.
    - First record encountered is preserved as primary; missing fields are merged
      from subsequent duplicates.

    Returns a :class:`DedupeResult` with the deduplicated list and statistics.
    """
    input_count = len(leads)

    # Index by (tva, city_lower) to handle branches
    by_tva: Dict[str, List[Lead]] = {}
    # Leads without a valid TVA are kept as-is
    no_tva: List[Lead] = []

    for lead in leads:
        if lead.tva:
            by_tva.setdefault(lead.tva, []).append(lead)
        else:
            no_tva.append(lead)

    deduplicated: List[Lead] = []
    duplicate_tvats: List[str] = []

    for tva, group in by_tva.items():
        if len(group) == 1:
            deduplicated.append(group[0])
            continue

        # Group contains duplicates — separate branches
        branches: List[List[Lead]] = []
        current_branch: List[Lead] = [group[0]]

        for candidate in group[1:]:
            is_new_branch = False
            for branch in branches:
                if _is_branch(branch[0], candidate):
                    is_new_branch = True
                    break
            if _is_branch(current_branch[0], candidate):
                branches.append(current_branch)
                current_branch = [candidate]
            else:
                current_branch.append(candidate)

        branches.append(current_branch)

        for branch in branches:
            primary = branch[0]
            for secondary in branch[1:]:
                _merge_into(primary, secondary)
            deduplicated.append(primary)

        if len(branches) == 1 and len(group) > 1:
            duplicate_tvats.append(tva)

    deduplicated.extend(no_tva)

    return DedupeResult(
        leads=deduplicated,
        duplicates=duplicate_tvats,
        input_count=input_count,
        output_count=len(deduplicated),
    )
