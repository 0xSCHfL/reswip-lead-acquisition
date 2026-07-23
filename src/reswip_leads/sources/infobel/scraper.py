"""Playwright scraper for Infobel Belgium category pages.

The scraper is intentionally source-specific and returns raw public business
details. It does not infer directors or company status; those remain KBO
responsibilities.
"""
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import urljoin


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
    infobel_url: str = ""
    scrape_date: str = ""


def _clean(value: str) -> str:
    return " ".join((value or "").split())


def _first_email(page) -> str:
    links = page.locator('a[href^="mailto:"]')
    if not links.count():
        return ""
    href = links.first.get_attribute("href")
    return (href or "").removeprefix("mailto:").strip()


def _first_external_link(page, current_url: str) -> str:
    for i in range(page.locator("a[href]").count()):
        href = page.locator("a[href]").nth(i).get_attribute("href") or ""
        absolute = urljoin(current_url, href)
        if absolute.startswith(("http://", "https://")) and "infobel.com" not in absolute:
            return absolute
    return ""


def _extract_phone(body: str) -> str:
    matches = re.findall(r"(?:\+32\s?\d[\d ./-]{7,}|0\d[\d ./-]{8,})", body)
    return _clean(matches[-1]) if matches else ""


def _extract_tva(url: str, body: str) -> str:
    match = re.search(r"BE\s?\d{4}[ .]?\d{3}[ .]?\d{3}", f"{url} {body}", re.I)
    if not match:
        return ""
    return "BE" + re.sub(r"\D", "", match.group(0))[2:]


class InfobelScraper:
    """Scrape all Infobel business details linked by a category page."""

    def __init__(self, *, executable_path: str = "/usr/bin/chromium", timeout_ms: int = 60_000):
        self.executable_path = executable_path
        self.timeout_ms = timeout_ms

    def scrape(self, category_url: str, limit: Optional[int] = None) -> List[InfobelRecord]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise RuntimeError("Install Playwright with: python3 -m pip install --user playwright") from exc

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=self.executable_path, args=["--no-sandbox"]
            )
            context = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
            page = context.new_page()
            page.goto(category_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(4_000)
            detail_urls = []
            for i in range(page.locator('a[href*="businessdetails"]').count()):
                href = page.locator('a[href*="businessdetails"]').nth(i).get_attribute("href") or ""
                absolute = urljoin(category_url, href)
                if absolute not in detail_urls:
                    detail_urls.append(absolute)

            records = []
            for detail_url in detail_urls[:limit] if limit else detail_urls:
                try:
                    records.append(self._scrape_detail(context, detail_url, category_url))
                except Exception:
                    # One blocked/malformed detail page must not discard the batch.
                    continue
            browser.close()
        return records

    def scrape_search(self, search_term: str, location: str) -> List[InfobelRecord]:
        """Submit Infobel's homepage search form, then scrape its results.

        ``search_term`` can be a category or business name (for example
        ``Restaurant``), while ``location`` can be a city or postal code.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Install Playwright with: python3 -m pip install --user playwright") from exc
        homepage = "https://www.infobel.com/fr/belgium/"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True, executable_path=self.executable_path, args=["--no-sandbox"]
            )
            page = browser.new_page()
            page.goto(homepage, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(3_000)
            term = page.locator("#search-term-input-header")
            if not term.count():
                term = page.locator('input[placeholder*="Qui ? Quoi"]')
            place = page.locator("#search-location-input-header")
            if not place.count():
                place = page.locator('input[placeholder*="Où ?"]')
            term.last.fill(search_term)
            place.last.fill(location)
            place.last.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(3_000)
            result_url = page.url
            browser.close()
        return self.scrape(result_url)

    def _scrape_detail(self, context, detail_url: str, category_url: str) -> InfobelRecord:
        page = context.new_page()
        try:
            page.goto(detail_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            page.wait_for_timeout(1_500)
            for label in ("Afficher le téléphone", "Envoyer un e-mail"):
                control = page.get_by_text(label, exact=True)
                if control.count():
                    try:
                        control.first.click(timeout=5_000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass
            body = page.locator("body").inner_text(timeout=10_000)
            lines = [_clean(line) for line in body.splitlines() if _clean(line)]
            name = _clean(page.locator("h1").first.inner_text()) if page.locator("h1").count() else ""
            email = _first_email(page)
            website = _first_external_link(page, detail_url)
            postal_match = re.search(r"\b(\d{4})\s+([^\n|]+)", body)
            return InfobelRecord(
                business_name=name,
                address=lines[lines.index(name) + 1] if name in lines and lines.index(name) + 1 < len(lines) else "",
                postal_code=postal_match.group(1) if postal_match else "",
                city=_clean(postal_match.group(2)) if postal_match else "",
                category="",
                phone=_extract_phone(body),
                email=email,
                website=website,
                tva=_extract_tva(detail_url, body),
                infobel_url=detail_url,
                scrape_date=date.today().isoformat(),
            )
        finally:
            page.close()

    @staticmethod
    def write_csv(records: Iterable[InfobelRecord], output_path: str) -> str:
        rows = [asdict(record) for record in records]
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(InfobelRecord.__dataclass_fields__))
            writer.writeheader()
            writer.writerows(rows)
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape an Infobel Belgium category page")
    parser.add_argument("--url", help="Infobel category URL")
    parser.add_argument("--search-term", help="Homepage search term, e.g. Restaurant")
    parser.add_argument("--location", help="Homepage search location, e.g. Aubel or 4880")
    parser.add_argument("--output", required=True, help="Output CSV path")
    parser.add_argument("--limit", type=int, default=None, help="Maximum detail pages to scrape")
    args = parser.parse_args()
    if args.url:
        records = InfobelScraper().scrape(args.url, limit=args.limit)
    elif args.search_term and args.location:
        records = InfobelScraper().scrape_search(args.search_term, args.location)
    else:
        parser.error("provide --url or both --search-term and --location")
    InfobelScraper.write_csv(records, args.output)
    print(f"Scraped {len(records)} Infobel businesses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
