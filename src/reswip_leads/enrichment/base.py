"""Base enrichment contract and shared HTTP helpers.

The public contact enrichment adapters (Pappers, KBO web) all share
the same result contract so the pipeline and any other consumer can
treat them uniformly:

    EnrichmentResult(
        fields={...},           # attribute → value (only non-empty)
        evidence=[...],         # one Evidence per filled field
        status=ENRICHED|NO_MATCH|ERROR,
        lookup_key="...",       # the primary key used (e.g. normalized TVA)
        source_url="...",       # the last URL that produced data
        error="",               # populated on ERROR
    )

Adapters never raise. Failures are reported through ``status=ERROR`` and
the ``error`` field. This makes orchestration in the pipeline trivial.

The module is sector-neutral. Insurance- or FSMA-specific assumptions
have been removed — the contract is the same for energy, insurance, and
any future sector.
"""
from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional


logger = logging.getLogger(__name__)


# ── Public result types ────────────────────────────────────────────


class EnrichmentStatus(str, Enum):
    """Outcome of an enrichment call."""

    ENRICHED = "enriched"
    NO_MATCH = "no_match"
    ERROR = "error"


# Confidence levels used in evidence. Kept as a plain string literal
# type to keep the dataclass JSON-friendly.
Confidence = str  # "high" | "medium" | "low"


@dataclass
class Evidence:
    """Provenance for a single field that was filled by an enricher."""

    source: str
    source_url: str
    field: str
    confidence: Confidence
    note: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "source": self.source,
            "source_url": self.source_url,
            "field": self.field,
            "confidence": self.confidence,
            "note": self.note,
        }


# Canonical set of fields an enricher may fill on a :class:`Lead`. Any
# other keys in :attr:`EnrichmentResult.fields` are preserved as-is
# (e.g. directors list for evidence purposes) but not auto-applied to
# a Lead by :meth:`BaseEnricher.enrich_dict`.
LEAD_FIELDS: tuple = (
    "first_name",
    "last_name",
    "position",
    "email",
    "phone",
    "mobile",
    "website",
    "address",
    "city",
    "postcode",
    "company_name",
)


@dataclass
class EnrichmentResult:
    """Structured result of an enrichment call.

    ``fields`` carries the actual values to apply. ``evidence`` lists
    one :class:`Evidence` per filled field so callers can audit the
    provenance of every change.
    """

    status: EnrichmentStatus
    fields: Dict[str, str] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)
    lookup_key: str = ""
    source_url: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Flatten to a dict so the pipeline and old-style callers can
        treat the result as a flat key-value mapping.

        Layout::

            {
                # Field values (subset of LEAD_FIELDS, plus any extras
                # the enricher chose to return, e.g. ``emails`` list).
                "first_name": "...",
                ...
                # Status / evidence metadata.
                "status": "enriched|no_match|error",
                "evidence": [ {source, source_url, field, confidence, note}, ... ],
                "source_url": "...",
                "lookup_key": "...",
                "error": "...",
            }
        """
        out: Dict[str, Any] = dict(self.fields)
        out["status"] = self.status.value
        out["source_url"] = self.source_url
        out["lookup_key"] = self.lookup_key
        out["error"] = self.error
        out["evidence"] = [e.to_dict() for e in self.evidence]
        return out

    def as_flat_dict(self) -> Dict[str, Any]:
        """Alias for :meth:`to_dict` — kept for API parity."""
        return self.to_dict()


# ── Configuration ──────────────────────────────────────────────────


@dataclass
class EnrichmentConfig:
    """Per-adapter configuration: timeout, retries, delay, proxy, UA."""

    timeout: float = 15.0
    retries: int = 2
    delay: float = 0.5
    proxy: Optional[Dict[str, str]] = None
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __post_init__(self) -> None:
        if self.timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {self.timeout}")
        if self.retries < 0:
            raise ValueError(f"retries must be >= 0, got {self.retries}")
        if self.delay < 0:
            raise ValueError(f"delay must be >= 0, got {self.delay}")
        if self.proxy is not None and not isinstance(self.proxy, dict):
            raise ValueError(
                f"proxy must be a dict (e.g. {{'http': '...'}}), got {type(self.proxy).__name__}"
            )


# ── Base enricher ──────────────────────────────────────────────────


class BaseEnricher(abc.ABC):
    """Abstract base for all contact-enrichment adapters.

    Subclasses implement :meth:`enrich` and return a structured
    :class:`EnrichmentResult`. The base class provides:

    - :meth:`enrich_dict` — a compatibility shim that flattens the
      result into the legacy dict shape the pipeline already consumes.
    - :meth:`merge_into` — apply fields to a target without
      overwriting non-empty values.
    - :meth:`_request` — the single place HTTP happens, so retries,
      proxy, delay, timeout, and UA are uniform across adapters.
    """

    SOURCE_NAME: str = "unknown"

    def __init__(
        self,
        config: Optional[EnrichmentConfig] = None,
        session: Optional[Any] = None,
    ) -> None:
        self.config = config or EnrichmentConfig()
        self._session = session  # may be None — _request creates a default
        self._last_request_at: Optional[float] = None

    # ── Public contract ─────────────────────────────────────────

    @abc.abstractmethod
    def enrich(
        self, tva: str, company_name: str = ""
    ) -> Dict[str, Any]:
        """Look up the company by TVA (and optionally by name) and
        return a flat dict of values plus status/evidence metadata.

        The returned dict always contains:

        - ``status`` — one of ``"enriched"``, ``"no_match"``, ``"error"``.
        - ``source_url`` — the last URL consulted (empty if never
          reached the network).
        - ``lookup_key`` — the primary lookup key used
          (digits-only TVA, possibly combined with the name).
        - ``evidence`` — list of ``{source, source_url, field,
          confidence, note}`` dicts, one per filled field.
        - ``error`` — empty unless ``status == "error"``.

        Plus any fields the adapter discovered (``first_name``,
        ``last_name``, ``position``, ``email``, ``phone``,
        ``website``, …) and any lists the adapter chose to expose
        (e.g. ``emails``, ``phones``, ``directors``).

        Implementations must never raise. Failures are reported
        through ``status="error"`` and the ``error`` field.
        """

    def enrich_dict(
        self, tva: str, company_name: str = ""
    ) -> Dict[str, Any]:
        """Alias for :meth:`enrich`.

        Kept for API clarity. Both methods return the same dict.
        """
        return self.enrich(tva, company_name)

    # ── Field application helpers ───────────────────────────────

    @staticmethod
    def merge_into(target: Any, attr: str, value: str) -> bool:
        """Set ``target.<attr>`` to ``value`` only if it is currently empty.

        Mirrors :func:`reswip_leads.pipeline.LeadPipeline._fill_if_empty`
        and the dedupe merge policy. Returns True if a value was set.

        ``target`` is typically a :class:`Lead` but any object with
        attribute access works.
        """
        if not value:
            return False
        current = getattr(target, attr, None)
        if current not in (None, ""):
            return False
        setattr(target, attr, value.strip() if isinstance(value, str) else value)
        return True

    @classmethod
    def apply_to_lead(
        cls, lead: Any, result: EnrichmentResult
    ) -> List[Evidence]:
        """Apply :attr:`EnrichmentResult.fields` to ``lead``, respecting
        the "never overwrite non-empty" policy.

        Returns the list of :class:`Evidence` entries that were
        actually applied (i.e. for fields that were empty on the lead
        and the enricher produced a non-empty value).
        """
        applied: List[Evidence] = []
        for evidence in result.evidence:
            if evidence.field not in LEAD_FIELDS:
                continue
            value = result.fields.get(evidence.field, "")
            if cls.merge_into(lead, evidence.field, value):
                applied.append(evidence)
        return applied

    # ── HTTP helper ─────────────────────────────────────────────

    # HTTP status codes that are considered transient and should be
    # retried up to ``config.retries`` times. 5xx covers server errors,
    # 429 is the standard rate-limit signal.
    _TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

    def _request(self, url: str, *, params: Optional[Dict[str, str]] = None) -> Any:
        """Single place HTTP happens.

        Honors :attr:`config.timeout`, :attr:`config.retries`,
        :attr:`config.delay`, :attr:`config.proxy`, and
        :attr:`config.user_agent`. The session used is the one passed
        at construction time, or a fresh :class:`requests.Session`
        if none was provided.

        Returns the final :class:`requests.Response`. The caller is
        responsible for inspecting ``status_code`` — that lets
        adapters distinguish 200-with-no-data (NO_MATCH) from
        500/network failure (ERROR). Transient HTTP status codes
        (5xx, 429) are retried up to ``config.retries`` times.
        """
        try:
            import requests  # local import so the module loads even
                             # when requests is absent in test envs
        except ImportError as exc:  # pragma: no cover - exercised in
                                     # environments without requests
            raise RuntimeError(
                "The 'requests' library is required for live HTTP enrichment. "
                "Install it with `pip install requests`."
            ) from exc

        session = self._session
        if session is None:
            session = requests.Session()
            session.headers.setdefault("User-Agent", self.config.user_agent)
            if self.config.proxy:
                session.proxies.update(self.config.proxy)

        last_exc: Optional[Exception] = None
        last_response: Any = None
        attempts = self.config.retries + 1
        for attempt in range(attempts):
            try:
                if self.config.delay and self._last_request_at is not None:
                    elapsed = time.monotonic() - self._last_request_at
                    remaining = self.config.delay - elapsed
                    if remaining > 0:
                        time.sleep(remaining)
                response = session.get(
                    url,
                    params=params,
                    timeout=self.config.timeout,
                    headers={"User-Agent": self.config.user_agent},
                )
                self._last_request_at = time.monotonic()
            except Exception as exc:  # noqa: BLE001 - any network failure
                last_exc = exc
                logger.debug(
                    "%s: HTTP attempt %d/%d failed for %s: %s",
                    self.SOURCE_NAME,
                    attempt + 1,
                    attempts,
                    url,
                    exc,
                )
                continue

            status_code = getattr(response, "status_code", 0)
            if status_code in self._TRANSIENT_STATUS_CODES and attempt < attempts - 1:
                last_response = response
                logger.debug(
                    "%s: HTTP attempt %d/%d got %d for %s; will retry",
                    self.SOURCE_NAME,
                    attempt + 1,
                    attempts,
                    status_code,
                    url,
                )
                continue
            return response

        # Either all attempts raised, or the last attempt was a
        # transient status we never recovered from. Prefer the last
        # response (so the adapter can read status_code) and only fall
        # back to re-raising the last exception if no response came
        # through.
        if last_response is not None:
            return last_response
        assert last_exc is not None
        raise last_exc


# ── Helpers exposed for adapters ──────────────────────────────────


def digits_only(value: str) -> str:
    """Return ``value`` with everything but digits stripped.

    Used to normalize a TVA into the form expected by Pappers and KBO
    URLs (no ``BE`` prefix, no spaces or dots).
    """
    return "".join(ch for ch in (value or "") if ch.isdigit())


def normalize_tva(value: str) -> str:
    """Normalize a TVA into ``BE##########`` form.

    Accepts the same inputs as :func:`reswip_leads.core.models.normalize_tva`
    but is duplicated here so this module is self-contained and can
    be imported without pulling the core package.
    """
    import re

    if not value:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(value)).upper()
    if cleaned.startswith("BE"):
        cleaned = cleaned[2:]
    return f"BE{cleaned}" if cleaned else ""


def confidence_for(field: str) -> Confidence:
    """Return a reasonable default confidence for ``field``.

    The values are advisory only; callers may override.
    """
    high = {"email", "first_name", "last_name", "company_name"}
    medium = {"position", "address", "city", "postcode"}
    if field in high:
        return "high"
    if field in medium:
        return "medium"
    return "low"


__all__ = [
    "BASE_LEAD_FIELDS" if False else "LEAD_FIELDS",  # keep export name stable
    "BaseEnricher",
    "Confidence",
    "EnrichmentConfig",
    "EnrichmentResult",
    "EnrichmentStatus",
    "Evidence",
    "confidence_for",
    "digits_only",
    "normalize_tva",
]
