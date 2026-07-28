"""Collect Infobel business detail URLs via search, save to CSV.

Workflow:
  1. Launch persistent Chromium (same profile as scrape_urls.py)
  2. Navigate to infobel.com, fill search form (sector + region)
  3. Collect all detail URLs from results pages (with pagination)
  4. Save to CSV with infobel_url column — ready for scrape_urls.py

Usage:
  python collect_links.py "Boulanger" "Bruxelles" -o links.csv
  python collect_links.py "Plombier" "Anvers" -o links.csv --limit 50
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

log = logging.getLogger("infobel_links")

_EXECUTABLE = "/usr/bin/chromium"
_INFOBEL_HOME = "https://www.infobel.com/fr/belgium/"

_CHALLENGE_MARKERS = (
    "#challenge-running",
    "#challenge-form",
    "cf-challenge",
    "cf_chl_opt",
    "Just a moment",
    "Checking your browser",
    "Verify you are human",
)


def _clean(value: str) -> str:
    return " ".join((value or "").split())


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
    log.info("═══════════════════════════════════════════════════")
    log.info("  %s — solving reCAPTCHA or waiting up to %ds",
             reason, timeout_ms // 1000)
    log.info("═══════════════════════════════════════════════════")

    # Try full reCAPTCHA solve first (audio challenge)
    if _try_solve_recaptcha(page):
        page.wait_for_timeout(5_000)
        # Check if page cleared
        try:
            page.wait_for_function(
                """() => {
                    const html = document.documentElement.innerHTML.toLowerCase();
                    const onAbuse = html.includes('/landing/abuse');
                    const onChallenge = html.includes('#challenge-running')
                        || html.includes('#challenge-form')
                        || html.includes('cf-challenge')
                        || html.includes('cf_chl_opt')
                        || html.includes('just a moment')
                        || html.includes('checking your browser')
                        || html.includes('verify you are human')
                        || html.includes('security verification');
                    return !onAbuse && !onChallenge;
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
        page.wait_for_function(
            """() => {
                const html = document.documentElement.innerHTML.toLowerCase();
                const onAbuse = html.includes('/landing/abuse');
                const onChallenge = html.includes('#challenge-running')
                    || html.includes('#challenge-form')
                    || html.includes('cf-challenge')
                    || html.includes('cf_chl_opt')
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


def _wait_for_challenge(page, headed: bool, timeout_ms: int = 300_000) -> bool:
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
            return True

        if not headed:
            log.warning("blocked (abuse=%s challenge=%s)", is_abuse, is_challenge)
            return False

        reason = "abuse redirect" if is_abuse else "Cloudflare challenge"
        cleared = _wait_for_human(page, reason, timeout_ms=timeout_ms)
        if cleared:
            page.wait_for_timeout(3_000)
            log.info("challenge cleared")
            try:
                page.reload(wait_until="domcontentloaded", timeout=15_000)
                page.wait_for_timeout(3_000)
            except Exception:
                pass
            return True
        else:
            log.warning("challenge did not clear within %ds", timeout_ms // 1000)
            return False
    return False


# ---------------------------------------------------------------------------
# Search form interaction
# ---------------------------------------------------------------------------

def _fill_search_form(page, sector: str, region: str) -> None:
    """Fill Infobel's homepage search form (Kendo UI)."""
    # Locate inputs — try header selectors first, fallback to placeholders
    term = page.locator("#search-term-input-header")
    if not term.count():
        term = page.locator('input[placeholder*="Qui"]')
    if not term.count():
        term = page.locator('input[placeholder*=" quoi"]')

    place = page.locator("#search-location-input-header")
    if not place.count():
        place = page.locator('input[placeholder*="Où"]')
    if not place.count():
        place = page.locator('input[placeholder*=" où"]')

    log.info("search term input found: %s, location input found: %s",
             term.count() > 0, place.count() > 0)

    # Fill sector — type and pick from Kendo dropdown
    term.last.click()
    page.wait_for_timeout(300)
    term.last.fill("")
    page.wait_for_timeout(200)
    term.last.type(sector, delay=80)
    log.info("typed sector: %r", sector)
    page.wait_for_timeout(2_000)

    # Pick from Kendo dropdown — try to find exact match, else skip (don't narrow search)
    _pick_kendo(page, sector, exact_match=sector)

    # Fill region — type and pick from Kendo dropdown
    place.last.click()
    page.wait_for_timeout(300)
    place.last.fill("")
    page.wait_for_timeout(200)
    place.last.type(region, delay=80)
    log.info("typed region: %r", region)
    page.wait_for_timeout(2_000)

    # Pick location from dropdown
    _pick_kendo(page, region, exact_match=region)

    # Click search
    btn = page.locator("#btn-search-header")
    if not btn.count():
        btn = page.get_by_text("Recherche", exact=True).first
    log.info("clicking search button")
    btn.click(timeout=10_000)


def _pick_kendo(page, typed_text: str, exact_match: str = "") -> bool:
    """Pick from Kendo dropdown only if exact match found. Returns True if picked."""
    try:
        items = page.locator(".k-list-container:not([style*='display: none']) .k-item")
        count = items.count()
        if count and exact_match:
            for i in range(count):
                text = (items.nth(i).text_content() or "").strip()
                if text.lower() == exact_match.lower():
                    log.info("kendo exact match: %r", text[:80])
                    items.nth(i).click(timeout=3_000)
                    page.wait_for_timeout(500)
                    return True
            log.info("no exact match for %r in %d kendo items — skipping", exact_match, count)
            return False
    except Exception as exc:
        log.debug("kendo exact match failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

def _get_total_results(page) -> int:
    """Try to extract total result count from the page."""
    try:
        body = page.locator("body").inner_text(timeout=5_000)
        patterns = [
            r"(\d[\d\s]*)\s*résultats?\b",
            r"sur\s+(\d[\d\s]*)",
        ]
        for pat in patterns:
            match = re.search(pat, body, re.I)
            if match:
                num = int(match.group(1).replace(" ", "").replace("\xa0", ""))
                if num > 0:
                    return num
    except Exception:
        pass
    return 0


def _click_next_page(page) -> bool:
    """Click the Next/› button in pagination. Returns True if clicked."""
    for sel in ["a:has-text('›')", "a:has-text('Next')", "a:has-text('»')"]:
        try:
            btn = page.locator(sel)
            if btn.count() and btn.first.is_visible():
                disabled = btn.first.evaluate(
                    "el => el.classList.contains('disabled') || el.parentElement.classList.contains('disabled')"
                )
                if not disabled:
                    log.info("clicking Next: %s", sel)
                    btn.first.dispatch_event("click")
                    page.wait_for_load_state("domcontentloaded", timeout=15_000)
                    page.wait_for_timeout(2_000)
                    return True
        except Exception:
            continue
    log.info("no Next button found")
    return False


def _wait_for_page_content_change(page, old_first_url: str, timeout_ms: int = 15_000) -> bool:
    """Wait until the first businessdetails link changes (page content updated)."""
    try:
        page.wait_for_function(
            f"""() => {{
                const links = document.querySelectorAll('a[href*="businessdetails"]');
                if (links.length === 0) return true;
                const first = links[0].href;
                return first !== {old_first_url!r};
            }}""",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        # Fallback: just wait and hope
        page.wait_for_timeout(3_000)
        return True


# ---------------------------------------------------------------------------
# Link collection
# ---------------------------------------------------------------------------

def _collect_links_from_page(page) -> list[str]:
    """Collect unique detail page URLs from the current results page."""
    urls: list[str] = []

    # Primary: businessdetails.aspx pages
    links = page.locator('a[href*="businessdetails"]')
    count = links.count()
    log.info("businessdetails links: %d", count)
    for i in range(count):
        href = links.nth(i).get_attribute("href") or ""
        absolute = urljoin(page.url, href)
        if absolute not in urls:
            urls.append(absolute)

    # Secondary: infobel_kapitol detail pages (same structure)
    links2 = page.locator('a[href*="infobel_kapitol"][href*="businessdetails"]')
    count2 = links2.count()
    log.info("infobel_kapitol detail links: %d", count2)
    for i in range(count2):
        href = links2.nth(i).get_attribute("href") or ""
        absolute = urljoin(page.url, href)
        if absolute not in urls:
            urls.append(absolute)

    log.info("collected %d unique detail URLs from page", len(urls))
    return urls


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def collect_links(
    sector: str,
    region: str,
    output: str | Path,
    *,
    headed: bool = True,
    limit: int | None = None,
    profile_dir: str = "~/.infobel-scrape-profile",
) -> int:
    """Search Infobel, collect detail URLs, save to CSV. Returns count of URLs found."""
    from playwright.sync_api import sync_playwright

    resolved_profile = Path(profile_dir).expanduser()
    resolved_profile.mkdir(parents=True, exist_ok=True)

    log.info("searching Infobel: sector=%r region=%r headed=%s", sector, region, headed)

    with sync_playwright() as pw:
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

        try:
            page = context.pages[0] if context.pages else context.new_page()

            # Navigate to homepage
            log.info("loading %s", _INFOBEL_HOME)
            page.goto(_INFOBEL_HOME, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(3_000)

            # Handle challenge on homepage
            if not _wait_for_challenge(page, headed):
                log.error("cannot get past challenge on homepage")
                return 0

            log.info("homepage loaded: %s", page.url)

            # Fill and submit search
            _fill_search_form(page, sector, region)

            # Wait for results page
            try:
                page.wait_for_url("**/BusinessResults**", timeout=30_000)
            except Exception:
                if "/Landing/Abuse" in page.url:
                    log.error("abuse redirect after search")
                    if headed:
                        _wait_for_challenge(page, headed)
                    else:
                        return 0
                else:
                    log.error("navigation to results failed: %s", page.url)
                    return 0

            page.wait_for_timeout(3_000)

            # Handle challenge on results page
            if not _wait_for_challenge(page, headed):
                log.error("cannot get past challenge on results page")
                return 0

            results_url = page.url
            log.info("results page: %s", results_url)

            # Get total results count
            total_results = _get_total_results(page)
            log.info("total results indicator: %d", total_results)

            # Collect all links across pages using click-based pagination
            all_urls: list[str] = []
            page_num = 1
            consecutive_empty = 0

            while True:
                if limit and len(all_urls) >= limit:
                    log.info("reached limit of %d URLs", limit)
                    break

                log.info("--- page %d (%d URLs so far) ---", page_num, len(all_urls))

                # Handle challenge/abuse on each page
                if not _wait_for_challenge(page, headed):
                    log.warning("challenge on page %d — stopping", page_num)
                    break

                # Get first link URL before collecting (for change detection)
                old_first = ""
                try:
                    first_link = page.locator('a[href*="businessdetails"]').first
                    if first_link.count():
                        old_first = first_link.get_attribute("href") or ""
                except Exception:
                    pass

                page_urls = _collect_links_from_page(page)

                # Debug: show first 3 URLs to verify page changed
                if page_urls:
                    for u in page_urls[:3]:
                        log.debug("  sample: %s", u[-60:])

                if not page_urls:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        log.info("2 consecutive empty pages — done")
                        break
                else:
                    consecutive_empty = 0

                new_count = 0
                for u in page_urls:
                    if u not in all_urls:
                        all_urls.append(u)
                        new_count += 1

                log.info("page %d: %d new URLs (total: %d)", page_num, new_count, len(all_urls))

                # If no new URLs found on this page, we've likely wrapped around or hit the end
                if new_count == 0 and page_num > 1:
                    log.info("no new URLs on page %d — pagination complete", page_num)
                    break

                page_num += 1

                # Click to next page
                if not _click_next_page(page):
                    log.info("no more pages")
                    break

                # Wait for page content to change
                page.wait_for_timeout(2_000)
                _wait_for_challenge(page, headed)

            # Apply limit
            if limit:
                all_urls = all_urls[:limit]

            log.info("═══ collected %d unique detail URLs ═══", len(all_urls))

            # Save to CSV
            out_path = Path(output)
            with out_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["infobel_url"])
                writer.writeheader()
                for url in all_urls:
                    writer.writerow({"infobel_url": url})

            log.info("saved to %s", out_path)
            return len(all_urls)

        finally:
            try:
                context.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Search Infobel and collect business detail URLs to CSV",
    )
    parser.add_argument("sector", help="Sector/activity to search (e.g. 'Boulanger', 'Plombier')")
    parser.add_argument("region", help="Region/city to search in (e.g. 'Bruxelles', 'Anvers')")
    parser.add_argument("-o", "--output", default="infobel_links.csv", help="Output CSV file (default: infobel_links.csv)")
    parser.add_argument("--headed", action="store_true", default=True, help="Run in headed mode (default: True)")
    parser.add_argument("--no-headed", action="store_true", help="Run in headless mode")
    parser.add_argument("--limit", type=int, default=None, help="Max URLs to collect")
    parser.add_argument("--profile-dir", default="~/.infobel-scrape-profile", help="Persistent Chromium profile directory")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        force=True,
    )

    headed = not getattr(args, "no_headed", False)

    count = collect_links(
        args.sector,
        args.region,
        args.output,
        headed=headed,
        limit=args.limit,
        profile_dir=args.profile_dir,
    )
    print(f"Collected {count} URLs → {args.output}")
    return 0 if count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
