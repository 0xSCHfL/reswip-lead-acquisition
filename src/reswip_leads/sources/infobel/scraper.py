"""Playwright scraper for Infobel Belgium category pages.

The scraper is intentionally source-specific and returns raw public business
details. It does not infer directors or company status; those remain KBO
responsibilities.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import re
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import parse_qs, urlparse, urljoin

log = logging.getLogger("infobel")

_INFOBEL_HOME = "https://www.infobel.com/fr/belgium/"
_RESULTS_PATH_RE = re.compile(r"/Search/BusinessResults")
_CHALLENGE_MARKERS = (
    "#challenge-running",
    "#challenge-form",
    "cf-challenge",
    "cf_chl_opt",
    "Just a moment",
    "Checking your browser",
    "Verify you are human",
)


def _token_summary(token: object) -> str:
    """Return a safe one-line summary of a token (never the full value)."""
    s = str(token)
    h = hashlib.sha256(s.encode()).hexdigest()[:12]
    return f"len={len(s)} prefix={s[:12]}… sha256={h}"


class InfobelSearchError(Exception):
    """Raised when the Infobel search flow fails."""


@dataclass
class InfobelRecord:
    business_name: str = ""
    address: str = ""
    postal_code: str = ""
    city: str = ""
    category: str = ""
    phone: str = ""
    email: str = ""
    website: str = ""
    tva: str = ""
    hours: str = ""
    infobel_url: str = ""
    financial_url: str = ""
    financial_company_name: str = ""
    financial_registered_office: str = ""
    financial_creation_date: str = ""
    financial_tva: str = ""
    financial_fiscal_year: str = ""
    financial_administrators: str = ""
    position: str = ""
    first_name: str = ""
    last_name: str = ""
    financial_nacebel: str = ""
    financial_employee_count: str = ""
    search_results_url: str = ""
    scrape_date: str = ""


def _clean(value: str) -> str:
    return " ".join((value or "").split())


def _first_email(page) -> str:
    links = page.locator('a[href^="mailto:"]')
    count = links.count()
    log.debug("mailto links found: %d", count)
    if not count:
        return ""
    href = links.first.get_attribute("href")
    email = (href or "").removeprefix("mailto:").strip()
    log.debug("email extracted: %s", email or "(empty)")
    return email


def _first_external_link(page, current_url: str) -> str:
    link_count = page.locator("a[href]").count()
    log.debug("total <a href> on page: %d", link_count)
    for i in range(link_count):
        href = page.locator("a[href]").nth(i).get_attribute("href") or ""
        absolute = urljoin(current_url, href)
        if absolute.startswith(("http://", "https://")) and "infobel.com" not in absolute:
            log.debug("external link found: %s", absolute)
            return absolute
    log.debug("no external link found")
    return ""


def _extract_phone(body: str) -> str:
    matches = re.findall(r"(?:\+32\s?\d[\d ./-]{7,}|0\d[\d ./-]{8,})", body)
    result = _clean(matches[-1]) if matches else ""
    log.debug("phone extraction: %d matches → %s", len(matches), result or "(empty)")
    return result


def _extract_tva(url: str, body: str) -> str:
    match = re.search(r"BE\s?\d{4}[ .]?\d{3}[ .]?\d{3}", f"{url} {body}", re.I)
    if not match:
        log.debug("body TVA extraction: no match")
        return ""
    tva = "BE" + re.sub(r"\D", "", match.group(0))[2:]
    log.debug("body TVA extraction: %s", tva)
    return tva


def _pick_kendo_item(page, selector: str) -> bool:
    """Click the first visible Kendo autocomplete/dropdown item.

    Returns ``True`` when an item was clicked, ``False`` otherwise.
    """
    try:
        items = page.locator(f"{selector} .k-item")
        count = items.count()
        log.debug("kendo items for '%s': %d", selector, count)
        if count:
            text = (items.first.text_content() or "").strip()[:60]
            items.first.click(timeout=3_000)
            page.wait_for_timeout(500)
            log.info("kendo item selected: %r", text)
            return True
    except Exception as exc:
        log.debug("kendo primary selector failed: %s", exc)
    try:
        containers = page.locator(".k-list-container")
        for i in range(containers.count()):
            container = containers.nth(i)
            if not container.evaluate("el => el.offsetParent !== null"):
                continue
            items = container.locator(".k-item")
            if items.count():
                text = (items.first.text_content() or "").strip()[:60]
                items.first.click(timeout=3_000)
                page.wait_for_timeout(500)
                log.info("kendo item selected (fallback): %r", text)
                return True
    except Exception as exc:
        log.debug("kendo fallback failed: %s", exc)
    log.warning("no kendo item picked for selector '%s'", selector)
    return False


_FINANCIAL_VAT_RE = re.compile(r"/financial/vat/(BE\d{10})\b", re.I)


def _extract_financial_link(page, detail_url: str) -> tuple[str, str]:
    """Return ``(financial_url, tva)`` from the detail page."""
    links = page.locator('a[href*="/financial/vat/"]')
    count = links.count()
    log.debug("financial vat href links: %d", count)
    if count:
        href = links.first.get_attribute("href") or ""
        absolute = urljoin(detail_url, href)
        match = _FINANCIAL_VAT_RE.search(absolute)
        if match:
            tva = match.group(1).upper()
            log.debug("financial TVA from href: %s url=%s", tva, absolute)
            return absolute, tva
        log.debug("financial link found but no TVA pattern: %s", absolute)
        return absolute, ""

    links = page.get_by_text("Informations financières", exact=False)
    count = links.count()
    log.debug("text 'Informations financières' links: %d", count)
    if count:
        href = links.first.get_attribute("href") or ""
        if href:
            absolute = urljoin(detail_url, href)
            match = _FINANCIAL_VAT_RE.search(absolute)
            if match:
                tva = match.group(1).upper()
                log.debug("financial TVA from text link: %s url=%s", tva, absolute)
                return absolute, tva
            log.debug("text financial link but no TVA: %s", absolute)
            return absolute, ""

    log.debug("no financial link found on %s", detail_url)
    return "", ""


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
    """Extract labeled financial fields from an Infobel financial page."""
    company = _financial_value(text, r"Nom de l'entreprise")
    office = _financial_value(text, r"Siège Social")
    creation = _financial_value(text, r"Date de création")
    tva = _financial_value(text, r"TVA")
    tva_match = re.search(r"BE\s?\d{4}[ .]?\d{3}[ .]?\d{3}", tva, re.I)
    tva = "BE" + re.sub(r"\D", "", tva_match.group(0)) if tva_match else ""
    fiscal = _financial_value(text, r"Année fiscale")
    administrators = _financial_raw_value(text, r"Administrateur(?:s)?")
    admin_parts = [_clean(part) for part in re.split(r"\n+", administrators) if _clean(part)]
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


def _is_challenge_page(page) -> bool:
    """Return True if the current page looks like a Cloudflare challenge."""
    # 1) Check page URL for Cloudflare challenge parameters
    url = page.url or ""
    if "__cf_chl" in url or "challenge" in url.lower():
        log.warning("challenge detected via URL: %s", url)
        return True
    # 2) Check page title
    try:
        title = page.title() or ""
    except Exception:
        title = ""
    if "just a moment" in title.lower() or "checking" in title.lower():
        log.warning("challenge detected via title: %r", title)
        return True
    # 3) Check HTML body for challenge markers
    try:
        html = page.content(timeout=5_000)
    except Exception:
        return False
    lower = html.lower()
    matched = [m for m in _CHALLENGE_MARKERS if m.lower() in lower]
    if matched:
        log.warning("challenge markers detected in HTML: %s", matched)
    else:
        log.debug("no challenge markers found (url=%s title=%r)", url, title)
    return bool(matched)


def _wait_for_challenge_to_clear(page, timeout_ms: int = 120_000) -> None:
    """Block until challenge indicators are gone and the search form is visible."""
    log.info("waiting up to %ds for challenge to clear…", timeout_ms // 1000)
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
                document.querySelector('#search-term-input-header')
                || document.querySelector('input[placeholder*="Qui"]')
            );
            return !onChallenge && formVisible;
        }""",
        timeout=timeout_ms,
    )
    log.info("challenge cleared — search form visible")


def _validate_results_url(url: str) -> None:
    """Raise InfobelSearchError if *url* is not a valid BusinessResults token URL."""
    parsed = urlparse(url)
    if parsed.hostname != "www.infobel.com":
        raise InfobelSearchError(
            f"Unexpected host {parsed.hostname!r}, expected www.infobel.com"
        )
    if not _RESULTS_PATH_RE.search(parsed.path):
        raise InfobelSearchError(
            f"Path {parsed.path!r} does not match /Search/BusinessResults"
        )
    qs = parse_qs(parsed.query)
    token = qs.get("token", [None])[0]
    if not token:
        raise InfobelSearchError(f"Missing or empty 'token' query parameter in {url}")


class InfobelScraper:
    """Scrape all Infobel business details linked by a category page."""

    def __init__(
        self,
        *,
        executable_path: str = "/usr/bin/chromium",
        timeout_ms: int = 60_000,
    ):
        self.executable_path = executable_path
        self.timeout_ms = timeout_ms

    def scrape(self, category_url: str, limit: Optional[int] = None) -> List[InfobelRecord]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError("Install Playwright with: python3 -m pip install --user playwright") from exc

        log.info("launching headless browser for category URL: %s", category_url)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=self.executable_path
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
            page = context.new_page()
            page.goto(category_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            log.info("category page loaded: %s", page.url)
            page.wait_for_timeout(4_000)
            detail_urls = []
            raw_count = page.locator('a[href*="businessdetails"]').count()
            log.info("raw detail hrefs on page: %d", raw_count)
            for i in range(raw_count):
                href = page.locator('a[href*="businessdetails"]').nth(i).get_attribute("href") or ""
                absolute = urljoin(category_url, href)
                if absolute not in detail_urls:
                    detail_urls.append(absolute)
            log.info("unique detail URLs: %d", len(detail_urls))

            records = []
            selected = detail_urls[:limit] if limit else detail_urls
            log.info("will scrape %d detail pages", len(selected))
            for detail_url in selected:
                try:
                    record = self._scrape_detail(context, detail_url, category_url)
                    records.append(record)
                    log.info("extracted: %s", record.business_name or "(no name)")
                except Exception:
                    log.warning("failed to scrape %s", detail_url, exc_info=True)
            browser.close()
        log.info("category scrape complete — %d records", len(records))
        return records

    def scrape_search(
        self,
        search_term: str,
        location: str,
        profile_dir: str | Path = "~/.infobel-profile",
        headed: bool = False,
        limit: Optional[int] = None,
    ) -> List[InfobelRecord]:
        """Submit Infobel's homepage search form, then scrape its results."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install Playwright with: python3 -m pip install --user playwright") from exc

        resolved_profile = Path(profile_dir).expanduser()
        resolved_profile.mkdir(parents=True, exist_ok=True)
        search_results_url = ""
        records: list[InfobelRecord] = []

        log.info(
            "search=%r location=%r headed=%s profile=%s limit=%s",
            search_term, location, headed, resolved_profile, limit,
        )
        log.info("launching persistent browser (headless=%s)", not headed)

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(resolved_profile),
                headless=not headed,
                executable_path=self.executable_path,
                channel="chromium" if not self.executable_path else None,
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            try:
                # ── Apply stealth patches ───────────────────────────────
                try:
                    from playwright_stealth import Stealth
                    stealth = Stealth()
                    stealth.apply_stealth_sync(context)
                    log.info("playwright-stealth applied to context")
                except ImportError:
                    log.debug("playwright-stealth not installed — skipping")
                except Exception as exc:
                    log.warning("stealth application failed: %s", exc)

                page = context.pages[0] if context.pages else context.new_page()
                log.info("opening %s", _INFOBEL_HOME)
                page.goto(_INFOBEL_HOME, wait_until="domcontentloaded", timeout=self.timeout_ms)
                log.info("page loaded — url=%s title=%r", page.url, page.title())
                page.wait_for_timeout(3_000)

                # ── Cloudflare / challenge detection ──────────────────────
                challenge = _is_challenge_page(page)
                if challenge:
                    log.warning("Cloudflare/challenge page detected")
                    if headed:
                        try:
                            _wait_for_challenge_to_clear(page, timeout_ms=120_000)
                        except Exception:
                            log.error("challenge not solved within 120 s")
                            raise InfobelSearchError(
                                "Cloudflare challenge was not solved within 120 s"
                            )
                    else:
                        log.error("challenge detected in headless mode — aborting")
                        raise InfobelSearchError(
                            "Cloudflare challenge detected in headless mode. "
                            "Re-run with --headed to solve it manually."
                        )
                else:
                    log.info("no challenge detected")

                # ── Fill search form (Kendo UI) ──────────────────────────
                term = page.locator("#search-term-input-header")
                term_count = term.count()
                if not term_count:
                    term = page.locator('input[placeholder*="Qui"]')
                    log.debug("fallback term selector, count=%d", term.count())
                log.info("search term input located (count=%d)", term_count or term.count())

                place = page.locator("#search-location-input-header")
                place_count = place.count()
                if not place_count:
                    place = page.locator('input[placeholder*="Où"]')
                    log.debug("fallback location selector, count=%d", place.count())
                log.info("location input located (count=%d)", place_count or place.count())

                term.last.click()
                page.wait_for_timeout(300)
                term.last.type(search_term, delay=80)
                log.info("search term typed: %r", search_term)
                page.wait_for_timeout(2_000)

                # ── Select term from Kendo dropdown ─────────────────────
                term_picked = _pick_kendo_item(
                    page, "#search-term-input-header_listbox"
                )

                # ── Fill and select location ────────────────────────────
                place.last.click()
                page.wait_for_timeout(300)
                place.last.type(location, delay=80)
                log.info("location typed: %r", location)
                page.wait_for_timeout(2_000)
                _pick_kendo_item(page, ".k-list-container:visible .k-item")

                # ── Submit: click Recherche button ─────────────────────
                search_btn = page.locator("#btn-search-header")
                if not search_btn.count():
                    log.warning("#btn-search-header not found, trying text match")
                    search_btn = page.get_by_text("Recherche", exact=True).first
                log.info("clicking Recherche button")
                search_btn.click(timeout=10_000)

                # Wait for natural navigation to BusinessResults
                try:
                    page.wait_for_url(
                        "**/BusinessResults**", timeout=30_000,
                    )
                except Exception:
                    if "/Landing/Abuse" in page.url:
                        raise InfobelSearchError(
                            "Infobel redirected to /Landing/Abuse — "
                            "bot detection triggered. Re-run with --headed "
                            "to solve it manually, or try deleting the "
                            "profile directory and re-running."
                        )
                    raise InfobelSearchError(
                        f"Navigation to BusinessResults timed out. "
                        f"Current URL: {page.url}"
                    )

                page.wait_for_timeout(3_000)
                search_results_url = page.url
                log.info("results page loaded: %s", search_results_url)

                # Check for abuse redirect
                if "/Landing/Abuse" in search_results_url:
                    log.warning(
                        "redirected to /Landing/Abuse — "
                        "abuse detection triggered"
                    )
                    raise InfobelSearchError(
                        "Infobel redirected to /Landing/Abuse — "
                        "bot detection triggered. Re-run with --headed "
                        "to solve it manually, or try deleting the "
                        "profile directory and re-running."
                    )

                # ── Pre-collection diagnostics ─────────────────────────
                result_html = page.content() or ""
                log.info("── pre-collection diagnostics ──")
                log.info("  page url      : %s", search_results_url)
                log.info("  page title    : %r", page.title())
                log.info("  html length   : %d", len(result_html))
                try:
                    heading = page.locator("h1").first.inner_text(timeout=3_000)
                    log.info("  page heading  : %r", heading.strip()[:100])
                except Exception:
                    log.debug("  page heading  : (none)")

                # ── Collect detail links ──────────────────────────────────
                raw_detail_count = page.locator('a[href*="businessdetails"]').count()
                log.info("raw detail hrefs on results page: %d", raw_detail_count)
                detail_urls: list[str] = []
                seen_hrefs: list[str] = []
                for i in range(raw_detail_count):
                    href = (
                        page.locator('a[href*="businessdetails"]')
                        .nth(i)
                        .get_attribute("href")
                        or ""
                    )
                    seen_hrefs.append(href)
                    absolute = urljoin(page.url, href)
                    if absolute not in detail_urls:
                        detail_urls.append(absolute)
                    else:
                        log.debug("duplicate href skipped: %s", href)
                log.info("all collected hrefs: %s", seen_hrefs)
                log.info("unique detail URLs: %d", len(detail_urls))
                for idx, u in enumerate(detail_urls):
                    log.debug("  detail[%d] = %s", idx, u)

                # ── Apply limit ──────────────────────────────────────────
                selected = detail_urls[:limit] if limit else detail_urls
                log.info(
                    "selected %d detail pages to scrape (limit=%s)",
                    len(selected), limit,
                )

                # ── Scrape detail pages ───────────────────────────────────
                for detail_url in selected:
                    log.info("scraping detail: %s", detail_url)
                    try:
                        record = self._scrape_detail(context, detail_url, search_results_url)
                        record.search_results_url = search_results_url
                        if not record.business_name:
                            log.warning("empty business name for %s", detail_url)
                        if not record.postal_code and not record.city:
                            log.warning("missing address data for %s", detail_url)
                        log.info(
                            "record extracted: name=%r city=%r tva=%r fin_url=%s",
                            record.business_name,
                            record.city,
                            record.tva or "(empty)",
                            bool(record.financial_url),
                        )
                        records.append(record)
                    except Exception:
                        log.error(
                            "detail extraction failed for %s", detail_url, exc_info=True,
                        )
                        continue

            except InfobelSearchError:
                raise
            except Exception:
                log.error("unexpected error in scrape_search", exc_info=True)
                raise
            finally:
                log.info("closing browser context")
                context.close()

        log.info(
            "search scrape complete — %d records from %s",
            len(records), search_results_url or "(no URL)",
        )
        return records

    def _scrape_detail(self, context, detail_url: str, source_url: str) -> InfobelRecord:
        page = context.new_page()
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(1_500)
            log.debug("detail page loaded url=%s title=%r", page.url, page.title())

            # Reveal phone / email behind click-to-reveal
            for label in ("Afficher le téléphone", "Envoyer un e-mail"):
                control = page.get_by_text(label, exact=True)
                if control.count():
                    try:
                        control.first.click(timeout=5_000)
                        page.wait_for_timeout(500)
                        log.debug("clicked reveal: %s", label)
                    except Exception:
                        log.debug("could not click reveal: %s", label)
                        pass

            body = page.locator("body").inner_text(timeout=10_000)
            lines = [_clean(line) for line in body.splitlines() if _clean(line)]

            # Business name from <h1>
            h1_count = page.locator("h1").count()
            name = ""
            if h1_count:
                name = _clean(page.locator("h1").first.inner_text())
                log.debug("h1 heading: %r", name[:80])
            else:
                log.debug("no <h1> found on detail page")

            email = _first_email(page)
            website = _first_external_link(page, detail_url)

            # Address: postal code + city
            postal_match = re.search(r"\b(\d{4})\s+([^\n|]+)", body)
            financial_url, financial_tva = _extract_financial_link(page, detail_url)
            body_tva = _extract_tva(detail_url, body)
            financial_fields: dict[str, str] = {}
            if financial_url:
                try:
                    financial_fields = self._scrape_financial_page(context, financial_url)
                except Exception:
                    log.warning("financial page extraction failed for %s", financial_url, exc_info=True)

            # Build address line
            address = ""
            if name and name in lines:
                idx = lines.index(name)
                if idx + 1 < len(lines):
                    address = lines[idx + 1]
            if not address:
                log.debug("could not derive address from line after business name")

            record = InfobelRecord(
                business_name=name,
                address=address,
                postal_code=postal_match.group(1) if postal_match else "",
                city=_clean(postal_match.group(2)) if postal_match else "",
                category="",
                phone=_extract_phone(body),
                email=email,
                website=website,
                tva=financial_tva or body_tva,
                infobel_url=detail_url,
                financial_url=financial_url,
                **financial_fields,
                scrape_date=date.today().isoformat(),
            )
            log.debug(
                "detail fields: name=%r postal=%r city=%r phone=%r email=%r website=%r tva=%r",
                record.business_name,
                record.postal_code,
                record.city,
                record.phone,
                record.email,
                record.website,
                record.tva,
            )
            return record
        finally:
            page.close()

    def _scrape_financial_page(self, context, financial_url: str) -> dict[str, str]:
        """Open and parse an Infobel financial page in the current context."""
        page = context.new_page()
        try:
            page.goto(financial_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(1_000)
            body = page.locator("body").inner_text(timeout=10_000)
            if "/Landing/Abuse" in page.url or "Informations financières" not in body:
                log.warning("financial page rejected as abuse/challenge: url=%s", page.url)
                return {}
            fields = _parse_financial_page_text(body)
            log.info(
                "financial fields extracted: company=%r administrators=%r employees=%r",
                fields["financial_company_name"],
                fields["financial_administrators"],
                fields["financial_employee_count"],
            )
            return fields
        finally:
            page.close()

    @staticmethod
    def write_csv(records: Iterable[InfibelRecord], output_path: str) -> str:
        rows = [asdict(record) for record in records]
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                fieldnames = [f.name for f in fields(InfobelRecord)]
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            log.info("CSV written: %s (%d rows)", path, len(rows))
        except Exception:
            log.error("CSV write failed: %s", path, exc_info=True)
            raise
        return str(path)


def _setup_logging(level_name: str, log_file: Optional[str] = None) -> None:
    """Configure root-level logging for the infobel package."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape an Infobel Belgium category page")
    parser.add_argument("--url", help="Infobel category URL")
    parser.add_argument("--search-term", help="Homepage search term, e.g. Restaurant")
    parser.add_argument("--location", help="Homepage search location, e.g. Aubel or 4880")
    parser.add_argument("--profile-dir", default=None, help="Persistent Chromium profile directory")
    parser.add_argument("--headed", action="store_true", help="Run Chromium in headed mode")
    parser.add_argument("--output", required=True, help="Output CSV path")
    parser.add_argument("--limit", type=int, default=None, help="Maximum detail pages to scrape")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log verbosity (default: INFO)",
    )
    parser.add_argument("--log-file", default=None, help="Also write logs to this file")
    args = parser.parse_args()

    _setup_logging(args.log_level, args.log_file)
    log.info("infobel scraper starting")

    scraper = InfobelScraper()
    if args.url:
        records = scraper.scrape(args.url, limit=args.limit)
    elif args.search_term and args.location:
        records = scraper.scrape_search(
            args.search_term,
            args.location,
            profile_dir=args.profile_dir or "~/.infobel-profile",
            headed=args.headed,
            limit=args.limit,
        )
    else:
        parser.error("provide --url or both --search-term and --location")
    InfobelScraper.write_csv(records, args.output)
    log.info("done — %d records scraped → %s", len(records), args.output)
    print(f"Scraped {len(records)} Infobel businesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
