"""Sector profile loader for YAML configuration files."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


_PROFILES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "profiles"


@dataclass
class Profile:
    """A loaded sector profile (e.g. energy, insurance)."""

    name: str
    description: str = ""
    sources: List[str] = field(default_factory=list)
    filters: Dict[str, List[str]] = field(default_factory=dict)
    enrichment: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


def load_profile(name: str, profiles_dir: Optional[Path] = None) -> Profile:
    """Load a YAML profile by name or full path.

    Accepts either a profile name (e.g. ``"energy"``) resolved from the
    profiles directory, or a full path to a YAML file.

    Raises ``FileNotFoundError`` if the profile file does not exist.
    """
    candidate = Path(name)
    if candidate.suffix in (".yaml", ".yml") and candidate.exists():
        path = candidate
    else:
        directory = profiles_dir or _PROFILES_DIR
        path = directory / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Profile not found: {path}")

    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    # Collect any unknown top-level keys into extra
    known_keys = {"name", "description", "sources", "filters", "enrichment"}
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    return Profile(
        name=raw.get("name", name),
        description=raw.get("description", ""),
        sources=raw.get("sources", []),
        filters=raw.get("filters", {}),
        enrichment=raw.get("enrichment", []),
        extra=extra,
    )


def list_profiles(profiles_dir: Optional[Path] = None) -> List[str]:
    """Return available profile names."""
    directory = profiles_dir or _PROFILES_DIR
    return sorted(p.stem for p in directory.glob("*.yaml"))
