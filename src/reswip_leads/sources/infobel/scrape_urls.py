"""Bulk-scrape Infobel business detail pages listed in a CSV.

Launches a fresh Chromium context (with playwright-stealth) for each URL
to minimize Cloudflare fingerprinting.  Retries on detection with
exponential backoff.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import random
import re
import sys
import time
from dataclasses import fields
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

log = logging.getLogger("infobel_urls")

_CHALLENGE_MARKERS = (
    "#challenge-running",
    "#challenge-form",
    "cf-challenge",
    "cf_chl_opt",
    "Just a moment",
    "Checking your browser",
    "Verify you are human",
)

_MAX_RETRIES = 3
_RETRY_DELAYS = [5, 15, 30]  # seconds between attempts

_EXECUTABLE = "/usr/bin/chromium"
_CDP_URL = "http://localhost:9222"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chromium/150.0.7871.186 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Helpers (copied/adapted from scraper.py to avoid circular imports)
# ---------------------------------------------------------------------------

def _clean(value: str) -> str:
    return " ".join((value or "").split())


def _first_email(page) -> str:
    links = page.locator('a[href^="mailto:"]')
    if not links.count():
        return ""
    href = links.first.get_attribute("href")
    return (href or "").removeprefix("mailto:").strip()


_SKIPPED_DOMAINS = (
    "ejustice.just.fgov.be", "economie.fgov.be", "facebook.com", "twitter.com",
    "linkedin.com", "instagram.com", "youtube.com", "google.", "nbb.be",
)


def _site_internet_link(page, current_url: str) -> str:
    """Find the link labelled 'Site internet' on the Infobel detail page."""
    for label in ("Site internet", "Website"):
        link = page.locator(f"a:has-text('{label}')")
        if link.count():
            href = link.first.get_attribute("href") or ""
            if href.startswith(("http://", "https://")):
                return href
    return ""


def _first_external_link(page, current_url: str) -> str:
    """Find first real business website on the page, skipping government/social domains."""
    count = page.locator("a[href]").count()
    for i in range(count):
        href = page.locator("a[href]").nth(i).get_attribute("href") or ""
        absolute = urljoin(current_url, href)
        if not (absolute.startswith(("http://", "https://")) and "infobel.com" not in absolute):
            continue
        if any(d in absolute.lower() for d in _SKIPPED_DOMAINS):
            continue
        return absolute
    # Fallback: first external link (even if skipped domain)
    for i in range(count):
        href = page.locator("a[href]").nth(i).get_attribute("href") or ""
        absolute = urljoin(current_url, href)
        if absolute.startswith(("http://", "https://")) and "infobel.com" not in absolute:
            return absolute
    return ""


def _extract_phone(page) -> str:
    phones = page.evaluate("""() => {
        const section = document.querySelector('[id^=phones-region]');
        if (!section) return '';
        const texts = section.querySelectorAll('.detail-text');
        return Array.from(texts).map(el => el.textContent.trim()).filter(Boolean).join('; ');
    }""")
    if phones:
        return phones
    body = page.locator("body").inner_text(timeout=5_000)
    matches = re.findall(r"(?:0[1-9][\d . -]{7,})", body)
    filtered = [m for m in matches if "/" not in m and len(m.replace(" ","").replace(".","").replace("-","")) >= 9]
    return _clean(filtered[-1]) if filtered else ""


def _extract_hours(page) -> str:
    """Extract business hours from the 'Heures d'ouvertures' section.

    Returns a compact string like: "Lu: 08:00-11:30; Ma: 08:00-11:30; ..."
    """
    day_map = {
        "Lu": "Lu", "Ma": "Ma", "Me": "Me", "Je": "Je",
        "Ve": "Ve", "Sa": "Sa", "Di": "Di",
    }
    # Try to find the hours table/block
    hours_section = page.locator('text=Heures d\'ouvertures')
    if not hours_section.count():
        hours_section = page.locator('text=Heures')
    if not hours_section.count():
        log.debug("no hours section found")
        return ""

    # Look for a table near the heading
    # Walk up to a common ancestor and find rows
    try:
        # Try finding a table that contains day abbreviations
        tables = page.locator("table")
        for t_idx in range(tables.count()):
            table = tables.nth(t_idx)
            text = table.inner_text(timeout=3_000)
            if any(d in text for d in day_map):
                rows = []
                trs = table.locator("tr")
                for r_idx in range(trs.count()):
                    cells = trs.nth(r_idx).locator("td, th")
                    cell_texts = []
                    for c_idx in range(cells.count()):
                        cell_texts.append(_clean(cells.nth(c_idx).inner_text(timeout=1_000)))
                    if len(cell_texts) >= 3:
                        day = cell_texts[0]
                        from_time = cell_texts[1]
                        to_time = cell_texts[2]
                        remarks = cell_texts[3] if len(cell_texts) > 3 else ""
                        # Only process rows that look like day entries
                        if any(day.startswith(d) for d in day_map):
                            entry = f"{day}: {from_time}-{to_time}"
                            if remarks:
                                entry += f" ({remarks})"
                            rows.append(entry)
                if rows:
                    result = "; ".join(rows)
                    log.debug("hours extracted: %s", result)
                    return result
    except Exception as exc:
        log.debug("hours table extraction failed: %s", exc)

    # Fallback: regex on body text
    try:
        body = page.locator("body").inner_text(timeout=5_000)
        # Match patterns like "Lu\n08:00\n11:30" or "Lu 08:00 11:30"
        pattern = r"(Lu|Ma|Me|Je|Ve|Sa|Di)\s+(\d{2}:\d{2})\s+(\d{2}:\d{2})"
        matches = re.findall(pattern, body)
        if matches:
            rows = [f"{d}: {o}-{c}" for d, o, c in matches]
            result = "; ".join(rows)
            log.debug("hours extracted (regex fallback): %s", result)
            return result
    except Exception:
        pass

    log.debug("no hours data found")
    return ""


def _extract_tva(url: str, body: str) -> str:
    match = re.search(r"BE\s?\d{4}[ .]?\d{3}[ .]?\d{3}", f"{url} {body}", re.I)
    if not match:
        return ""
    return "BE" + re.sub(r"\D", "", match.group(0))[2:]


_FINANCIAL_VAT_RE = re.compile(r"/financial/vat/(BE\d{10})\b", re.I)
_FINANCIAL_LABELS = (
    r"Nom de l'entreprise", r"Siège Social", r"Date de création", r"TVA",
    r"Année fiscale", r"Administrateur(?:s)?", r"Classification Nacebel",
    r"Nombre d['’]employés",
)
_FINANCIAL_LABEL_BOUNDARY = "|".join(
    (*_FINANCIAL_LABELS, r"Autres liens:?", r"NOS SERVICES", r"SÉLECTIONNEZ UN PAYS")
)


def _financial_value(text: str, label: str) -> str:
    pattern = rf"{label}\s*(.*?)(?=(?:{_FINANCIAL_LABEL_BOUNDARY})|$)"
    match = re.search(pattern, text, re.I | re.S)
    return _clean(match.group(1)) if match else ""


def _financial_raw_value(text: str, label: str) -> str:
    pattern = rf"{label}\s*(.*?)(?=(?:{_FINANCIAL_LABEL_BOUNDARY})|$)"
    match = re.search(pattern, text, re.I | re.S)
    return match.group(1) if match else ""


def _parse_financial_page_text(text: str) -> dict[str, str]:
    company = _financial_value(text, r"Nom de l'entreprise")
    office = _financial_value(text, r"Siège Social")
    creation = _financial_value(text, r"Date de création")
    tva = _financial_value(text, r"TVA")
    tva_match = re.search(r"BE\s?\d{4}[ .]?\d{3}[ .]?\d{3}", tva, re.I)
    tva = "BE" + re.sub(r"\D", "", tva_match.group(0)) if tva_match else ""
    fiscal = _financial_value(text, r"Année fiscale")
    administrators = _financial_raw_value(text, r"Administrateur(?:s)?")
    admin_parts = [_clean(p) for p in re.split(r"\n+", administrators) if _clean(p)]
    nacebel = _financial_value(text, r"Classification Nacebel")
    employees = _financial_value(text, r"Nombre d['’]employés")

    position = ""
    first_name = ""
    last_name = ""
    if admin_parts:
        position = "Administrateur"
        name = admin_parts[0].strip()
        parts = name.split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

    return {
        "financial_company_name": company,
        "financial_registered_office": office,
        "financial_creation_date": creation,
        "financial_tva": tva,
        "financial_fiscal_year": fiscal,
        "financial_administrators": "; ".join(admin_parts),
        "position": position,
        "first_name": first_name,
        "last_name": last_name,
        "financial_nacebel": nacebel,
        "financial_employee_count": employees,
    }


# ---------------------------------------------------------------------------
# Challenge / Cloudflare detection
# ---------------------------------------------------------------------------

def _is_challenge_page(page) -> bool:
    url = page.url or ""
    if "__cf_chl" in url or "challenge" in url.lower():
        return True
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    if "just a moment" in title.lower() or "checking" in title.lower():
        return True
    try:
        html = page.content(timeout=5_000)
    except Exception:
        return False
    lower = html.lower()
    return any(m.lower() in lower for m in _CHALLENGE_MARKERS)


def _wait_for_challenge_to_clear(page, timeout_ms: int = 90_000) -> bool:
    """Wait for challenge to resolve. Returns True if cleared."""
    try:
        page.wait_for_function(
            """() => {
                const html = document.documentElement.innerHTML.toLowerCase();
                const markers = [
                    '#challenge-running', '#challenge-form',
                    'cf-challenge', 'cf_chl_opt',
                    'just a moment', 'checking your browser', 'verify you are human',
                ];
                const onChallenge = markers.some(m => html.includes(m));
                const formVisible = !!(
                    document.querySelector('.business-details')
                    || document.querySelector('h1')
                    || document.querySelector('[class*="detail"]')
                );
                return !onChallenge || formVisible;
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Financial page scraping
# ---------------------------------------------------------------------------

def _scrape_financial(page, financial_url: str) -> dict[str, str]:
    try:
        page.goto(financial_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(1_500)
        if "/Landing/Abuse" in page.url:
            log.warning("financial page redirected to abuse: %s", financial_url)
            return {}
        body = page.locator("body").inner_text(timeout=10_000)
        if "Informations financières" not in body:
            log.warning("financial page missing expected content: %s", financial_url)
            return {}
        return _parse_financial_page_text(body)
    except Exception as exc:
        log.warning("financial page failed: %s — %s", financial_url, exc)
        return {}


def _extract_financial_link(page, detail_url: str) -> tuple[str, str]:
    links = page.locator('a[href*="/financial/vat/"]')
    if links.count():
        href = links.first.get_attribute("href") or ""
        absolute = urljoin(detail_url, href)
        match = _FINANCIAL_VAT_RE.search(absolute)
        if match:
            return absolute, match.group(1).upper()
        return absolute, ""

    links = page.get_by_text("Informations financières", exact=False)
    if links.count():
        href = links.first.get_attribute("href") or ""
        if href:
            absolute = urljoin(detail_url, href)
            match = _FINANCIAL_VAT_RE.search(absolute)
            if match:
                return absolute, match.group(1).upper()
            return absolute, ""

    return "", ""


# ---------------------------------------------------------------------------
# Single-URL scraping
# ---------------------------------------------------------------------------

def _try_solve_recaptcha(page) -> bool:
    """Try to auto-solve reCAPTCHA using custom audio challenge solver.
    Returns True if solved successfully."""
    try:
        from .recaptcha_solver import solve_recaptcha_v2_sync
        return solve_recaptcha_v2_sync(page)
    except Exception as exc:
        log.debug("reCAPTCHA auto-solve failed: %s", exc)
    return False


def _try_click_turnstile(page) -> bool:
    """Try to auto-click reCAPTCHA/Turnstile checkbox. Returns True if clicked."""
    # Try reCAPTCHA v2 — target the anchor iframe (first one), not the challenge iframe
    try:
        rc_frame = page.frame_locator("iframe[src*='recaptcha/api2/anchor']")
        checkbox = rc_frame.locator(".recaptcha-checkbox-border, #recaptcha-anchor")
        if checkbox.count():
            log.info("auto-clicking reCAPTCHA checkbox")
            checkbox.first.click(timeout=5_000)
            page.wait_for_timeout(5_000)
            return True
    except Exception as exc:
        log.debug("reCAPTCHA auto-click failed: %s", exc)
    # Try Cloudflare Turnstile
    try:
        turnstile = page.frame_locator("iframe[src*='challenges.cloudflare.com']")
        checkbox = turnstile.locator("input[type='checkbox']")
        if checkbox.count():
            log.info("auto-clicking Turnstile checkbox")
            checkbox.click(timeout=5_000)
            page.wait_for_timeout(3_000)
            return True
    except Exception as exc:
        log.debug("Turnstile auto-click failed: %s", exc)
    return False


def _wait_for_human(page, reason: str, timeout_ms: int = 300_000) -> bool:
    """In headed mode: try solving reCAPTCHA, then wait for page to change.
    Returns True if the page navigated away from the problem."""
    log.info(">>> %s — solving reCAPTCHA or waiting up to %ds <<<",
             reason, timeout_ms // 1000)

    # Try full reCAPTCHA solve first (audio challenge)
    if _try_solve_recaptcha(page):
        page.wait_for_timeout(5_000)
        # Check if page cleared
        try:
            page.wait_for_function(
                """() => {
                    const url = window.location.href;
                    const html = document.documentElement.innerHTML.toLowerCase();
                    return !url.includes('Landing/Abuse')
                        && !html.includes('challenge-running')
                        && !html.includes('challenge-form')
                        && !html.includes('just a moment')
                        && !html.includes('checking your browser')
                        && !html.includes('verify you are human')
                        && !html.includes('security verification');
                }""",
                timeout=15_000,
            )
            return True
        except Exception:
            pass

    # Fallback: just click checkbox
    _try_click_turnstile(page)
    page.wait_for_timeout(5_000)

    try:
        # Poll every 2s: check if URL or content changed
        page.wait_for_function(
            """() => {
                const url = window.location.href;
                const html = document.documentElement.innerHTML.toLowerCase();
                const onAbuse = url.includes('Landing/Abuse');
                const onChallenge = html.includes('challenge-running')
                    || html.includes('challenge-form')
                    || html.includes('just a moment')
                    || html.includes('checking your browser')
                    || html.includes('verify you are human')
                    || html.includes('security verification');
                return !onAbuse && !onChallenge;
            }""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


def _dismiss_consent(page) -> None:
    try:
        consent = page.locator("#__abconsent-cmp")
        if consent.is_visible():
            accept = consent.locator("button:has-text('Accepter')")
            if accept.count():
                accept.click(timeout=5_000)
                page.wait_for_timeout(1_000)
                return
            accept = consent.locator("button:has-text('Accept')")
            if accept.count():
                accept.click(timeout=5_000)
                page.wait_for_timeout(1_000)
                return
        page.evaluate("""() => {
            const el = document.querySelector('#__abconsent-cmp');
            if (el) el.style.display = 'none';
        }""")
    except Exception:
        pass


def _accept_terms(page) -> None:
    """Accept Infobel terms and conditions overlay if present."""
    try:
        accept = page.get_by_text("Accept the conditions", exact=True)
        if accept.count():
            accept.click(timeout=5_000)
            page.wait_for_timeout(2_000)
            return
    except Exception:
        pass


def scrape_tab(page, url: str, *, headed: bool = False) -> dict[str, str]:
    """Scrape one infobel detail page in an existing tab."""
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(5_000)

    # Accept terms overlay if present (required before seeing business data)
    _accept_terms(page)
    page.wait_for_timeout(2_000)

    # ── Challenge / abuse handling ─────────────────────────────
    max_wait = 300_000 if headed else 60_000

    for attempt in range(3):
        is_abuse = "/Landing/Abuse" in page.url
        is_challenge = _is_challenge_page(page)

        is_verification = False
        try:
            body_check = page.locator("body").inner_text(timeout=3_000)
            is_verification = ("security verification" in body_check.lower()
                               or "performing" in body_check.lower())
        except Exception:
            pass

        if not is_abuse and not is_challenge and not is_verification:
            break

        if not headed:
            log.warning("blocked (abuse=%s challenge=%s) for %s", is_abuse, is_challenge, url)
            return {}

        reason = "abuse redirect" if is_abuse else "Cloudflare challenge"
        cleared = _wait_for_human(page, reason, timeout_ms=max_wait)
        if cleared:
            page.wait_for_timeout(3_000)
            log.info("page cleared after human intervention")
            try:
                page.reload(wait_until="domcontentloaded", timeout=15_000)
                page.wait_for_timeout(3_000)
                page.wait_for_selector("h1", timeout=10_000)
            except Exception:
                pass
            break
        else:
            log.warning("page did not clear after %ds", max_wait // 1000)
            return {}
    else:
        log.warning("still blocked after 3 attempts: %s", url)
        return {}

    # ── Extract data ──────────────────────────────────────────
    # Hide consent and click phone reveal button via JS
    page.evaluate("""() => {
        const cmp = document.querySelector('#__abconsent-cmp');
        if (cmp) cmp.style.display = 'none';
    }""")
    page.wait_for_timeout(500)
    page.evaluate("""() => {
        const els = document.querySelectorAll('[class*="detail-text"]');
        for (const el of els) {
            if (el.textContent.trim() === 'Afficher le téléphone') {
                el.click(); break;
            }
        }
    }""")
    page.wait_for_timeout(3_000)

    phone_data = page.evaluate("""() => {
        const section = document.querySelector('[id^=phones-region]');
        if (section) {
            const texts = section.querySelectorAll('.detail-text');
            return 'FOUND: ' + Array.from(texts).map(el => el.textContent.trim()).filter(Boolean).join('; ');
        }
        return 'NO SECTION';
    }""")
    log.info("phone debug: %s", phone_data)
    body = page.locator("body").inner_text(timeout=10_000)
    lines = [_clean(line) for line in body.splitlines() if _clean(line)]

    name = ""
    if page.locator("h1").count():
        name = _clean(page.locator("h1").first.inner_text())

    _BAD_NAMES = {"www.infobel.com", "performing security verification", "just a moment", ""}
    if name.lower().strip() in _BAD_NAMES or "cloudflare" in name.lower():
        log.warning("placeholder name detected (%r): %s", name, url)
        return {}

    _PLACEHOLDER_ADDR = {"modifier les infos", "afficher le téléphone", "envoyer un e-mail", "permalien"}
    address = ""
    if name and name in lines:
        idx = lines.index(name)
        if idx + 1 < len(lines):
            candidate = lines[idx + 1]
            if candidate.lower().strip() not in _PLACEHOLDER_ADDR:
                address = candidate

    postal_match = re.search(r"\b(\d{4})\s+([^\n|]+)", body)

    # Extract non-financial fields BEFORE navigating to financial page
    email = _first_email(page)
    website = _site_internet_link(page, url) or _first_external_link(page, url)
    hours = _extract_hours(page)

    financial_url, financial_tva = _extract_financial_link(page, url)
    body_tva = _extract_tva(url, body)

    financial_fields: dict[str, str] = {}
    if financial_url:
        financial_fields = _scrape_financial(page, financial_url)

    # Fallback: use financial registered office if address is still empty
    if not address and financial_fields.get("financial_registered_office"):
        address = financial_fields["financial_registered_office"]

    return {
        "business_name": name,
        "address": address,
        "postal_code": postal_match.group(1) if postal_match else "",
        "city": _clean(postal_match.group(2)) if postal_match else "",
        "category": "",
        "phone": _extract_phone(page),
        "email": email,
        "website": website,
        "tva": financial_tva or body_tva,
        "hours": hours,
        "financial_url": financial_url,
        **financial_fields,
        "scrape_date": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# CSV processing
# ---------------------------------------------------------------------------

def _needs_scraping(row: dict) -> bool:
    """Return True if the row has a URL but is missing key scraped fields."""
    url = (row.get("infobel_url") or "").strip()
    if not url or not url.startswith("http"):
        return False
    # If business_name is empty or still looks like a Cloudflare placeholder, scrape
    name = (row.get("business_name") or "").strip()
    if not name or name.lower() in ("www.infobel.com", "performing security verification"):
        return True
    # Also scrape if TVA and address are both missing
    tva = (row.get("tva") or "").strip()
    addr = (row.get("address") or "").strip()
    if not tva and not addr:
        return True
    return False


def process_csv(csv_path: str, *, headed: bool = False, dry_run: bool = False, limit: int | None = None, profile_dir: str = "~/.infobel-scrape-profile") -> int:
    """Read CSV, scrape missing rows, write back. Returns count of updated rows."""
    path = Path(csv_path)
    if not path.exists():
        log.error("CSV not found: %s", path)
        return 0

    with path.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    # Ensure scrape_date column exists
    if "scrape_date" not in fieldnames:
        fieldnames.append("scrape_date")

    # Discover all possible scraped fields so DictWriter includes them
    _SCRAPED_FIELDS = [
        "business_name", "address", "postal_code", "city", "category",
        "phone", "email", "website", "tva", "hours", "financial_url",
        "financial_company_name", "financial_registered_office",
        "financial_creation_date", "financial_tva", "financial_fiscal_year",
        "financial_administrators", "position", "first_name", "last_name",
        "financial_nacebel", "financial_employee_count",
    ]
    for f in _SCRAPED_FIELDS:
        if f not in fieldnames:
            fieldnames.append(f)

    to_scrape = [(i, row) for i, row in enumerate(rows) if _needs_scraping(row)]
    if limit:
        to_scrape = to_scrape[:limit]
    log.info("found %d rows needing scrape out of %d total", len(to_scrape), len(rows))

    if not to_scrape:
        log.info("nothing to scrape")
        return 0

    if dry_run:
        for idx, row in to_scrape:
            log.info("[dry-run] would scrape: %s → %s", row.get("business_name", "?"), row["infobel_url"])
        return len(to_scrape)

    from playwright.sync_api import sync_playwright

    resolved_profile = Path(profile_dir).expanduser()
    resolved_profile.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        # Launch ONE browser, reuse it for all URLs
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(resolved_profile),
            headless=not headed,
            executable_path=_EXECUTABLE,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1280, "height": 900},
            locale="fr-BE",
            timezone_id="Europe/Brussels",
        )

        def _write_csv():
            with path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        try:
            updated = 0
            for attempt_num, (idx, row) in enumerate(to_scrape, 1):
                url = row["infobel_url"]
                log.info("[%d/%d] scraping: %s", attempt_num, len(to_scrape), url)

                success = False
                for retry in range(_MAX_RETRIES):
                    page = context.new_page()
                    try:
                        data = scrape_tab(page, url, headed=headed)
                        if data and data.get("business_name"):
                            _PLACEHOLDERS = {"www.infobel.com", "performing security verification", "just a moment"}
                            for key, val in data.items():
                                old = (row.get(key) or "").strip()
                                if not old or old.lower() in _PLACEHOLDERS:
                                    row[key] = val
                            row["scrape_date"] = date.today().isoformat()
                            updated += 1
                            log.info("  → OK: %s", data.get("business_name", "?"))
                            _write_csv()
                            success = True
                            break
                        else:
                            log.warning("  → empty result (attempt %d/%d)", retry + 1, _MAX_RETRIES)
                    except (OSError, BrokenPipeError, EOFError) as exc:
                        log.warning("  → pipe error (attempt %d/%d): %s", retry + 1, _MAX_RETRIES, exc)
                    except Exception as exc:
                        log.warning("  → error (attempt %d/%d): %s", retry + 1, _MAX_RETRIES, exc)
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass

                    if retry < _MAX_RETRIES - 1:
                        delay = _RETRY_DELAYS[min(retry, len(_RETRY_DELAYS) - 1)]
                        log.info("  → retrying in %ds…", delay)
                        time.sleep(delay)

                if not success:
                    log.error("  → FAILED after %d attempts: %s", _MAX_RETRIES, url)
                    row["scrape_date"] = date.today().isoformat()
                    _write_csv()
                    if attempt_num < len(to_scrape):
                        extra = random.uniform(20, 40)
                        log.info("  → abuse detected, cooling down %.0fs…", extra)
                        time.sleep(extra)

                if attempt_num < len(to_scrape):
                    delay = random.uniform(8, 15)
                    log.info("  → waiting %.1fs before next URL…", delay)
                    time.sleep(delay)

        finally:
            try:
                context.close()
            except Exception:
                pass

    log.info("CSV updated: %s (%d rows scraped)", path, updated)
    return updated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        force=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape Infobel detail pages listed in a CSV (single browser, tabs per URL)",
    )
    parser.add_argument("csv", help="Path to the CSV file to update")
    parser.add_argument("--headed", action="store_true", help="Run Chromium in headed mode")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be scraped without doing it")
    parser.add_argument("--limit", type=int, default=None, help="Max rows to scrape (for testing)")
    parser.add_argument("--profile-dir", default="~/.infobel-scrape-profile", help="Persistent Chromium profile directory")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)
    log.info("infobel URL scraper starting")

    updated = process_csv(args.csv, headed=args.headed, dry_run=args.dry_run, limit=args.limit, profile_dir=args.profile_dir)
    print(f"Scraped/updated {updated} rows in {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
