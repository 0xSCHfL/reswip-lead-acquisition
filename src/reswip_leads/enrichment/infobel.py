"""Infobel fallback enrichment for missing contact info.

Delegates all browser/captcha work to the standalone Infobel pipeline
(``reswip_leads.sources.infobel.pipeline``), which is proven to handle
the Infobel abuse page and reCAPTCHA correctly.

``enrich_batch(leads)`` is called once per pipeline run (before the
per-lead enrichment loop) so that **one browser session** serves all
TVAs.  Per-lead ``enrich(tva)`` reads from an internal cache.
"""
from __future__ import annotations

import csv
import logging
import re
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional

from reswip_leads.core.models import Lead

logger = logging.getLogger(__name__)


class InfobelEnricher:
    """Infobel enrichment adapter.

    Opens **one** persistent Chromium browser (via subprocess to the
    working Infobel pipeline) to search all leads needing enrichment.
    Results are cached and served by ``enrich(tva)``.

    Call ``close()`` to clean up temp files.
    """

    SOURCE_NAME = "infobel"

    def __init__(
        self,
        headed: bool = False,
        profile_dir: str = "~/.infobel-enricher-profile",
        log_file: str | Path | None = None,
    ):
        self._headed = headed
        self._profile_dir = profile_dir
        self._log_file = Path(log_file) if log_file else None
        self._results: Dict[str, Dict[str, str]] = {}

    # ── Public API ──────────────────────────────────────────────

    def enrich_batch(self, leads: List[Lead]) -> None:
        """Pre-fetch Infobel data for all leads still missing contact info."""
        missing = [
            l for l in leads
            if l.tva and not (l.email and l.phone and l.website)
        ]
        if not missing:
            logger.info("all leads already have contact info — skipping Infobel batch")
            return

        logger.info(
            "Infobel batch: %d leads need enrichment",
            len(missing),
        )

        tmp_in = Path(tempfile.mktemp(suffix="_infobel_input.csv"))
        tmp_out = tmp_in.with_name(tmp_in.stem + "_out.csv")

        try:
            with tmp_in.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["tva"])
                writer.writeheader()
                for lead in missing:
                    digits = re.sub(r"\D", "", lead.tva)
                    if digits:
                        writer.writerow({"tva": digits})

            cmd = [
                sys.executable,
                "-m",
                "reswip_leads.sources.infobel.pipeline",
                "--input-csv",
                str(tmp_in),
                "-o",
                str(tmp_out),
                "--log-level",
                "WARNING",
                "--profile-dir",
                self._profile_dir,
            ]
            # Always run headed — the reCAPTCHA solver needs a visible browser window.
            cmd.append("--headed")

            logger.info(
                "calling Infobel pipeline (headed=%s) for %d TVAs…",
                self._headed,
                len(missing),
            )
            log_context = (
                self._log_file.open("a", encoding="utf-8")
                if self._log_file
                else nullcontext(None)
            )
            with log_context as child_log:
                result = subprocess.run(
                    cmd,
                    timeout=600,
                    stdout=child_log,
                    stderr=subprocess.STDOUT if child_log else None,
                )

            # ── Parse results ───────────────────────────────
            if tmp_out.exists() and tmp_out.stat().st_size > 0:
                with tmp_out.open(encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        tva = (row.get("search_tva") or "").strip()
                        if tva:
                            self._results[tva] = dict(row)
                logger.info(
                    "Infobel batch: cached %d / %d results",
                    len(self._results),
                    len(missing),
                )
            else:
                logger.warning("Infobel pipeline produced no output")

        except subprocess.TimeoutExpired:
            logger.error("Infobel pipeline timed out after 600s")
        except Exception as exc:
            logger.error("Infobel batch failed: %s", exc)
        finally:
            try:
                tmp_in.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                tmp_out.unlink(missing_ok=True)
            except Exception:
                pass

    def enrich(self, tva: str, company_name: Optional[str] = None) -> Dict[str, Any]:
        if not tva:
            return {"status": "no_match"}
        digits = re.sub(r"\D", "", tva)

        row = self._results.get(digits)
        if row is None:
            return {"status": "no_match"}

        has_data = any([
            row.get("email"),
            row.get("phone"),
            row.get("website"),
        ])
        if not has_data:
            return {"status": "no_match"}

        return {
            "status": "enriched",
            "email": row.get("email", ""),
            "phone": row.get("phone", ""),
            "website": row.get("website", ""),
            "source_url": row.get("infobel_url", ""),
        }

    def close(self) -> None:
        self._results.clear()
