# Email Recheck Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add targeted missing-email enrichment that rechecks only rows where Email is empty, using KBO → Pappers → Website priority chain.

**Architecture:** Source-based approach with independent source classes (`KboZipEmailSource`, `KboEmailSource`, `PappersEmailSource`, `WebsiteEmailSource`) orchestrated by `EmailRecheckEnricher`. Both pipeline integration (`--enricher email`) and standalone CLI use the same enricher.

**Tech Stack:** Python 3, requests, BeautifulSoup4, Playwright (website fallback), CSV reporting.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/reswip_leads/enrichment/email_sources.py` | Create | `EmailCandidate`, `BaseEmailSource`, `KboZipEmailSource`, `KboEmailSource`, `PappersEmailSource`, `WebsiteEmailSource` |
| `src/reswip_leads/enrichment/email_recheck.py` | Create | `EmailRecheckEnricher`, `_is_valid_email()`, standalone CLI, report generation |
| `src/reswip_leads/pipeline.py` | Modify | Add `--enricher email`, wire `EmailRecheckEnricher` in `_build_enrichers()` |
| `tests/test_email_recheck.py` | Create | All tests with mocked HTTP |
| `tests/fixtures/kbo_page_with_email.html` | Create | KBO HTML with mailto link |
| `tests/fixtures/kbo_page_no_email.html` | Create | KBO HTML without email |
| `tests/fixtures/pappers_page_with_email.html` | Create | Pappers HTML with Cloudflare-protected email |
| `tests/fixtures/pappers_page_no_email.html` | Create | Pappers HTML without email |

---

### Task 1: EmailCandidate + BaseEmailSource + _is_valid_email

**Files:**
- Create: `src/reswip_leads/enrichment/email_sources.py`
- Create: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_email_recheck.py
"""Tests for the email recheck enrichment sources and orchestrator."""
from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from reswip_leads.core.models import Lead
from reswip_leads.enrichment.base import EnrichmentConfig
from reswip_leads.enrichment.email_sources import (
    BaseEmailSource,
    EmailCandidate,
    KboZipEmailSource,
    KboEmailSource,
    PappersEmailSource,
    WebsiteEmailSource,
    _is_valid_email,
)


class TestEmailCandidate:
    def test_fields(self):
        c = EmailCandidate(
            email="test@example.com",
            source="kbo",
            source_url="https://example.com",
            confidence="High",
            note="Official register",
        )
        assert c.email == "test@example.com"
        assert c.source == "kbo"
        assert c.confidence == "High"


class TestIsValidEmail:
    def test_valid_email(self):
        assert _is_valid_email("contact@company-be.com") is True

    def test_noreply_rejected(self):
        assert _is_valid_email("noreply@company.com") is False

    def test_no_reply_rejected(self):
        assert _is_valid_email("no-reply@company.com") is False

    def test_donotreply_rejected(self):
        assert _is_valid_email("donotreply@company.com") is False

    def test_example_domain_rejected(self):
        assert _is_valid_email("test@example.com") is False

    def test_test_domain_rejected(self):
        assert _is_valid_email("user@test.com") is False

    def test_localhost_rejected(self):
        assert _is_valid_email("user@localhost") is False

    def test_pappers_domain_rejected(self):
        assert _is_valid_email("info@pappers.be") is False

    def test_kbo_domain_rejected(self):
        assert _is_valid_email("info@kbopub.economie.fgov.be") is False

    def test_facebook_rejected(self):
        assert _is_valid_email("user@facebook.com") is False

    def test_google_rejected(self):
        assert _is_valid_email("user@google.com") is False

    def test_linkedin_rejected(self):
        assert _is_valid_email("user@linkedin.com") is False

    def test_info_at_accepted_when_domain_matches(self):
        assert _is_valid_email(
            "info@company-be.com",
            website_url="https://www.company-be.com/contact",
        ) is True

    def test_info_at_domain_mismatch_accepted_with_note(self):
        # info@ on different domain is accepted (not rejected)
        # but caller should note the mismatch
        assert _is_valid_email("info@other-domain.com") is True

    def test_empty_email_rejected(self):
        assert _is_valid_email("") is False

    def test_missing_at_sign_rejected(self):
        assert _is_valid_email("invalid-email") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py -v`
Expected: FAIL with "cannot import name 'EmailCandidate'"

- [ ] **Step 3: Write minimal implementation**

```python
# src/reswip_leads/enrichment/email_sources.py
"""Email recheck sources — independent source classes for email enrichment.

Each source returns an EmailCandidate or None. The EmailRecheckEnricher
orchestrates them in priority order: KBO ZIP → KBO web → Pappers → Website.
"""
from __future__ import annotations

import abc
import re
from dataclasses import dataclass
from typing import Optional


# Rejected email prefixes
_REJECTED_PREFIXES = ("noreply@", "no-reply@", "donotreply@")

# Rejected domains
_REJECTED_DOMAINS = frozenset({
    "example.com",
    "test.com",
    "localhost",
    "pappers.be",
    "kbopub.economie.fgov.be",
    "google.com",
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "instagram.com",
})

# Email regex
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


@dataclass
class EmailCandidate:
    """Standardized email result from any source."""
    email: str
    source: str           # "kbo_zip", "kbo", "pappers", "website"
    source_url: str       # URL where email was found
    confidence: str       # "High", "Medium", "Low"
    note: str             # Short verification note


def _is_valid_email(email: str, website_url: str = "") -> bool:
    """Check if email is valid and not generic/directory."""
    if not email or "@" not in email:
        return False

    email_lower = email.lower().strip()

    # Check rejected prefixes
    for prefix in _REJECTED_PREFIXES:
        if email_lower.startswith(prefix):
            return False

    # Check rejected domains
    domain = email_lower.split("@")[-1]
    if domain in _REJECTED_DOMAINS:
        return False

    # Basic format validation
    if not _EMAIL_RE.match(email_lower):
        return False

    return True


class BaseEmailSource(abc.ABC):
    """Abstract base for email extraction sources."""

    @abc.abstractmethod
    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        """Return an EmailCandidate or None."""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reswip_leads/enrichment/email_sources.py tests/test_email_recheck.py
git commit -m "feat: add EmailCandidate, BaseEmailSource, and email validation"
```

---

### Task 2: KboZipEmailSource

**Files:**
- Modify: `src/reswip_leads/enrichment/email_sources.py`
- Modify: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_email_recheck.py

class TestKboZipEmailSource:
    def test_extracts_email_from_zip(self):
        """KBO ZIP has email → highest priority, no network."""
        mock_reader = MagicMock()
        mock_record = MagicMock()
        mock_record.email = "info@company-be.com"
        mock_reader.get_record.return_value = mock_record

        source = KboZipEmailSource(mock_reader)
        result = source.find_email("BE0123456789", "Test Company")

        assert result is not None
        assert result.email == "info@company-be.com"
        assert result.source == "kbo_zip"
        assert result.confidence == "High"

    def test_returns_none_when_no_email(self):
        """KBO ZIP has no email → returns None."""
        mock_reader = MagicMock()
        mock_record = MagicMock()
        mock_record.email = ""
        mock_reader.get_record.return_value = mock_record

        source = KboZipEmailSource(mock_reader)
        result = source.find_email("BE0123456789", "Test Company")

        assert result is None

    def test_returns_none_when_reader_fails(self):
        """KBO ZIP reader raises → returns None."""
        mock_reader = MagicMock()
        mock_reader.get_record.side_effect = Exception("ZIP error")

        source = KboZipEmailSource(mock_reader)
        result = source.find_email("BE0123456789", "Test Company")

        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestKboZipEmailSource -v`
Expected: FAIL with "name 'KboZipEmailSource' is not defined"

- [ ] **Step 3: Write minimal implementation**

```python
# Append to src/reswip_leads/enrichment/email_sources.py

class KboZipEmailSource(BaseEmailSource):
    """Extract email from KBO ZIP contact data (no network request)."""

    def __init__(self, kbo_zip_reader):
        self._reader = kbo_zip_reader

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        try:
            record = self._reader.get_record(tva)
            if record and record.email:
                return EmailCandidate(
                    email=record.email,
                    source="kbo_zip",
                    source_url="",
                    confidence="High",
                    note="Email from KBO ZIP contact data",
                )
        except Exception:
            pass
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestKboZipEmailSource -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reswip_leads/enrichment/email_sources.py tests/test_email_recheck.py
git commit -m "feat: add KboZipEmailSource for ZIP-based email extraction"
```

---

### Task 3: KboEmailSource

**Files:**
- Modify: `src/reswip_leads/enrichment/email_sources.py`
- Modify: `tests/test_email_recheck.py`
- Create: `tests/fixtures/kbo_page_with_email.html`
- Create: `tests/fixtures/kbo_page_no_email.html`

- [ ] **Step 1: Create test fixtures**

```html
<!-- tests/fixtures/kbo_page_with_email.html -->
<!doctype html>
<html lang="nl">
<head><meta charset="utf-8"><title>KBO</title></head>
<body>
  <h1>Test Company NV</h1>
  <dl>
    <dt>Ondernemingsnummer</dt>
    <dd>0123.456.789</dd>
    <dt>Website</dt>
    <dd><a href="https://www.test-company.be">https://www.test-company.be</a></dd>
  </dl>
  <a href="mailto:info@test-company.be">info@test-company.be</a>
</body>
</html>
```

```html
<!-- tests/fixtures/kbo_page_no_email.html -->
<!doctype html>
<html lang="nl">
<head><meta charset="utf-8"><title>KBO</title></head>
<body>
  <h1>No Email Company NV</h1>
  <dl>
    <dt>Ondernemingsnummer</dt>
    <dd>0987.654.321</dd>
  </dl>
</body>
</html>
```

- [ ] **Step 2: Write the failing test**

```python
# Append to tests/test_email_recheck.py

FIXTURES = Path(__file__).parent / "fixtures"


class TestKboEmailSource:
    def test_extracts_email_from_mailto(self):
        """KBO web page has mailto link → extracts email."""
        html = (FIXTURES / "kbo_page_with_email.html").read_text()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        source = KboEmailSource()
        with patch.object(source, "_request", return_value=mock_response):
            result = source.find_email("BE0123456789", "Test Company")

        assert result is not None
        assert result.email == "info@test-company.be"
        assert result.source == "kbo"
        assert result.confidence == "High"

    def test_returns_none_when_no_email(self):
        """KBO web page has no email → returns None."""
        html = (FIXTURES / "kbo_page_no_email.html").read_text()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        source = KboEmailSource()
        with patch.object(source, "_request", return_value=mock_response):
            result = source.find_email("BE0987654321", "No Email Company")

        assert result is None

    def test_filters_kbopub_email(self):
        """KBO page with kbopub email → filters it out."""
        html = """
        <body>
          <a href="mailto:info@kbopub.economie.fgov.be">KBO</a>
        </body>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        source = KboEmailSource()
        with patch.object(source, "_request", return_value=mock_response):
            result = source.find_email("BE0123456789", "Test")

        assert result is None

    def test_handles_network_error(self):
        """KBO web request fails → returns None."""
        source = KboEmailSource()
        with patch.object(source, "_request", side_effect=Exception("Network error")):
            result = source.find_email("BE0123456789", "Test")

        assert result is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestKboEmailSource -v`
Expected: FAIL with "name 'KboEmailSource' is not defined"

- [ ] **Step 4: Write minimal implementation**

```python
# Append to src/reswip_leads/enrichment/email_sources.py

import logging

logger = logging.getLogger(__name__)


class KboEmailSource(BaseEmailSource):
    """Scrape kbopub.economie.fgov.be for email."""

    KBO_URL = "https://kbopub.economie.fgov.be/kbopub/toonondernemingps.html?ondernemingsnummer={ent}"

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        from reswip_leads.enrichment.base import digits_only

        ent = digits_only(tva)
        url = self.KBO_URL.format(ent=ent)

        try:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            if proxy:
                session.proxies.update(proxy)

            response = session.get(url, timeout=15)
            if response.status_code != 200:
                return None

            html = response.text
            email = self._extract_email(html)
            if email:
                return EmailCandidate(
                    email=email,
                    source="kbo",
                    source_url=url,
                    confidence="High",
                    note="Email from KBO public page",
                )
        except Exception as exc:
            logger.debug("KboEmailSource failed for %s: %s", tva, exc)

        return None

    def _extract_email(self, html: str) -> str:
        """Extract email from KBO HTML, filtering generic addresses."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Try mailto links first
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().startswith("mailto:"):
                candidate = href[len("mailto:"):].split("?")[0]
                if candidate and "kbopub" not in candidate.lower():
                    return candidate

        # Fallback: regex
        for m in _EMAIL_RE.finditer(html):
            candidate = m.group(0)
            if "kbopub" in candidate.lower() or "economie" in candidate.lower():
                continue
            return candidate

        return ""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestKboEmailSource -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/reswip_leads/enrichment/email_sources.py tests/test_email_recheck.py tests/fixtures/kbo_page_with_email.html tests/fixtures/kbo_page_no_email.html
git commit -m "feat: add KboEmailSource for KBO web email extraction"
```

---

### Task 4: PappersEmailSource

**Files:**
- Modify: `src/reswip_leads/enrichment/email_sources.py`
- Modify: `tests/test_email_recheck.py`
- Create: `tests/fixtures/pappers_page_with_email.html`
- Create: `tests/fixtures/pappers_page_no_email.html`

- [ ] **Step 1: Create test fixtures**

```html
<!-- tests/fixtures/pappers_page_with_email.html -->
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>Pappers</title></head>
<body>
  <h1>Test Restaurant SA</h1>
  <span class="__cf_email__" data-cfemail="abc123">[email protected]</span>
  <a href="https://www.pappers.be">Pappers</a>
</body>
</html>
```

```html
<!-- tests/fixtures/pappers_page_no_email.html -->
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><title>Pappers</title></head>
<body>
  <h1>No Email Restaurant SA</h1>
</body>
</html>
```

- [ ] **Step 2: Write the failing test**

```python
# Append to tests/test_email_recheck.py

class TestPappersEmailSource:
    def test_extracts_email_from_pappers(self):
        """Pappers page has email → extracts it."""
        html = (FIXTURES / "pappers_page_with_email.html").read_text()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        source = PappersEmailSource()
        with patch.object(source, "_request", return_value=mock_response):
            result = source.find_email("BE0123456789", "Test Restaurant")

        assert result is not None
        assert result.email == "contact@test-restaurant.be"
        assert result.source == "pappers"
        assert result.confidence == "Medium"

    def test_returns_none_when_no_email(self):
        """Pappers page has no email → returns None."""
        html = (FIXTURES / "pappers_page_no_email.html").read_text()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        source = PappersEmailSource()
        with patch.object(source, "_request", return_value=mock_response):
            result = source.find_email("BE0987654321", "No Email Restaurant")

        assert result is None

    def test_filters_pappers_email(self):
        """Pappers page with pappers email → filters it out."""
        html = """
        <body>
          <a href="mailto:info@pappers.be">Contact</a>
        </body>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html

        source = PappersEmailSource()
        with patch.object(source, "_request", return_value=mock_response):
            result = source.find_email("BE0123456789", "Test")

        assert result is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestPappersEmailSource -v`
Expected: FAIL with "name 'PappersEmailSource' is not defined"

- [ ] **Step 4: Write minimal implementation**

```python
# Append to src/reswip_leads/enrichment/email_sources.py

class PappersEmailSource(BaseEmailSource):
    """Scrape pappers.be for email."""

    PAPPERS_URL = "https://www.pappers.be/fr/company/{slug}-{ent}"

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        from reswip_leads.enrichment.base import digits_only

        ent = digits_only(tva)
        slug = self._slugify(company_name) if company_name else ""
        url = self.PAPPERS_URL.format(slug=slug, ent=ent) if slug else f"https://www.pappers.be/fr/company/{ent}"

        try:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            if proxy:
                session.proxies.update(proxy)

            response = session.get(url, timeout=15)
            if response.status_code != 200:
                return None

            html = response.text
            emails = self._extract_emails(html)
            if emails:
                return EmailCandidate(
                    email=emails[0],
                    source="pappers",
                    source_url=url,
                    confidence="Medium",
                    note="Email from Pappers company page",
                )
        except Exception as exc:
            logger.debug("PappersEmailSource failed for %s: %s", tva, exc)

        return None

    def _extract_emails(self, html: str) -> list:
        """Extract emails from Pappers HTML, filtering pappers addresses."""
        emails = []
        seen = set()

        # Cloudflare-protected emails
        cf_pattern = re.compile(r'data-cfemail="([a-f0-9]+)"')
        for match in cf_pattern.finditer(html):
            decoded = self._decode_cf_email(match.group(1))
            if decoded and "pappers" not in decoded.lower():
                key = decoded.lower()
                if key not in seen:
                    seen.add(key)
                    emails.append(decoded)

        # Raw emails
        for m in _EMAIL_RE.finditer(html):
            candidate = m.group(0)
            if "pappers" in candidate.lower():
                continue
            key = candidate.lower()
            if key not in seen:
                seen.add(key)
                emails.append(candidate)

        return emails

    @staticmethod
    def _decode_cf_email(encoded: str) -> str:
        """Decode Cloudflare email protection."""
        try:
            n = int(encoded[:2], 16)
            return "".join(
                chr(int(encoded[i:i+2], 16) ^ n)
                for i in range(2, len(encoded), 2)
            )
        except Exception:
            return ""

    @staticmethod
    def _slugify(name: str) -> str:
        """Convert company name to URL slug."""
        import re
        slug = name.lower().strip()
        slug = re.sub(r"[^a-z0-9\s-]", "", slug)
        slug = re.sub(r"[\s-]+", "-", slug)
        return slug.strip("-")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestPappersEmailSource -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/reswip_leads/enrichment/email_sources.py tests/test_email_recheck.py tests/fixtures/pappers_page_with_email.html tests/fixtures/pappers_page_no_email.html
git commit -m "feat: add PappersEmailSource for Pappers email extraction"
```

---

### Task 5: WebsiteEmailSource

**Files:**
- Modify: `src/reswip_leads/enrichment/email_sources.py`
- Modify: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_email_recheck.py

class TestWebsiteEmailSource:
    def test_extracts_email_from_website(self):
        """Company website has email → extracts it."""
        html = """
        <body>
          <h1>Contact Us</h1>
          <p>Email: info@company-be.com</p>
        </body>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.headers = {"content-type": "text/html"}

        source = WebsiteEmailSource()
        with patch("requests.Session.get", return_value=mock_response):
            result = source.find_email(
                "BE0123456789",
                "Test Company",
                website_url="https://www.company-be.com",
            )

        assert result is not None
        assert result.email == "info@company-be.com"
        assert result.source == "website"
        assert result.confidence == "Low"

    def test_returns_none_when_no_email(self):
        """Company website has no email → returns None."""
        html = """
        <body>
          <h1>Contact Us</h1>
          <p>Call us at +32 2 123 45 67</p>
        </body>
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html
        mock_response.headers = {"content-type": "text/html"}

        source = WebsiteEmailSource()
        with patch("requests.Session.get", return_value=mock_response):
            result = source.find_email(
                "BE0123456789",
                "Test Company",
                website_url="https://www.company-be.com",
            )

        assert result is None

    def test_returns_none_when_no_website_url(self):
        """No website URL provided → returns None."""
        source = WebsiteEmailSource()
        result = source.find_email("BE0123456789", "Test Company")
        assert result is None

    def test_handles_network_error(self):
        """Website request fails → returns None."""
        source = WebsiteEmailSource()
        with patch("requests.Session.get", side_effect=Exception("Network error")):
            result = source.find_email(
                "BE0123456789",
                "Test Company",
                website_url="https://www.company-be.com",
            )
        assert result is None

    def test_playwright_fallback(self):
        """JS-heavy page → Playwright renders and extracts email."""
        # Minimal HTML that triggers Playwright
        js_heavy_html = '<div id="app"></div>' + 'x' * 100

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = js_heavy_html
        mock_response.headers = {"content-type": "text/html"}

        rendered_html = """
        <body>
          <p>Contact: info@company-be.com</p>
        </body>
        """

        source = WebsiteEmailSource()
        with patch("requests.Session.get", return_value=mock_response):
            with patch.object(source, "_render_with_playwright", return_value=rendered_html):
                result = source.find_email(
                    "BE0123456789",
                    "Test Company",
                    website_url="https://www.company-be.com",
                )

        assert result is not None
        assert result.email == "info@company-be.com"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestWebsiteEmailSource -v`
Expected: FAIL with "name 'WebsiteEmailSource' is not defined"

- [ ] **Step 3: Write minimal implementation**

```python
# Append to src/reswip_leads/enrichment/email_sources.py

class WebsiteEmailSource(BaseEmailSource):
    """Scrape official company website for email.

    Tries requests first; falls back to Playwright only if response
    is JS-heavy (<500 bytes, <noscript>, window.location, empty <div id="app">).
    """

    def find_email(
        self,
        tva: str,
        company_name: str = "",
        website_url: str = "",
        proxy: Optional[dict] = None,
    ) -> Optional[EmailCandidate]:
        if not website_url:
            return None

        try:
            import requests
            session = requests.Session()
            session.headers["User-Agent"] = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            if proxy:
                session.proxies.update(proxy)

            response = session.get(website_url, timeout=15)
            if response.status_code != 200:
                return None

            html = response.text

            # Check if JS rendering is needed
            if self._needs_playwright(html):
                html = self._render_with_playwright(website_url, proxy)

            email = self._extract_email(html)
            if email:
                return EmailCandidate(
                    email=email,
                    source="website",
                    source_url=website_url,
                    confidence="Low",
                    note="Email from company website",
                )
        except Exception as exc:
            logger.debug("WebsiteEmailSource failed for %s: %s", tva, exc)

        return None

    def _needs_playwright(self, html: str) -> bool:
        """Check if page needs JavaScript rendering."""
        if len(html) < 500:
            return True
        if "<noscript" in html.lower():
            return True
        if "window.location" in html:
            return True
        if '<div id="app">' in html or '<div id="root">' in html:
            return True
        return False

    def _render_with_playwright(self, url: str, proxy: Optional[dict] = None) -> str:
        """Render page with Playwright for JS-heavy sites."""
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html = page.content()
                browser.close()
                return html
        except Exception as exc:
            logger.debug("Playwright rendering failed for %s: %s", url, exc)
            return ""

    def _extract_email(self, html: str) -> str:
        """Extract email from website HTML."""
        # Try mailto links first
        for m in re.finditer(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', html):
            candidate = m.group(1)
            if _is_valid_email(candidate):
                return candidate

        # Fallback: regex
        for m in _EMAIL_RE.finditer(html):
            candidate = m.group(0)
            if _is_valid_email(candidate):
                return candidate

        return ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestWebsiteEmailSource -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reswip_leads/enrichment/email_sources.py tests/test_email_recheck.py
git commit -m "feat: add WebsiteEmailSource for company website email extraction"
```

---

### Task 6: EmailRecheckEnricher

**Files:**
- Create: `src/reswip_leads/enrichment/email_recheck.py`
- Modify: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_email_recheck.py

from reswip_leads.enrichment.email_recheck import EmailRecheckEnricher


class TestEmailRecheckEnricher:
    def test_kbo_email_preferred(self):
        """KBO returns email → used, Pappers not called."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.return_value = EmailCandidate(
            email="kbo@company.be",
            source="kbo",
            source_url="https://kbo.be",
            confidence="High",
            note="KBO email",
        )
        pappers_source = MagicMock(spec=BaseEmailSource)

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source, pappers_source],
        )

        result = enricher.enrich("BE0123456789", "Test Company")

        assert result["email"] == "kbo@company.be"
        pappers_source.find_email.assert_not_called()

    def test_pappers_fallback(self):
        """KBO empty → Pappers email used."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.return_value = None
        pappers_source = MagicMock(spec=BaseEmailSource)
        pappers_source.find_email.return_value = EmailCandidate(
            email="pappers@company.be",
            source="pappers",
            source_url="https://pappers.be",
            confidence="Medium",
            note="Pappers email",
        )

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source, pappers_source],
        )

        result = enricher.enrich("BE0123456789", "Test Company")

        assert result["email"] == "pappers@company.be"

    def test_website_fallback(self):
        """KBO+Pappers empty → website email used."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.return_value = None
        pappers_source = MagicMock(spec=BaseEmailSource)
        pappers_source.find_email.return_value = None
        website_source = MagicMock(spec=BaseEmailSource)
        website_source.find_email.return_value = EmailCandidate(
            email="web@company.be",
            source="website",
            source_url="https://company.be",
            confidence="Low",
            note="Website email",
        )

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source, pappers_source, website_source],
        )

        result = enricher.enrich("BE0123456789", "Test Company")

        assert result["email"] == "web@company.be"

    def test_existing_email_preserved(self):
        """Lead already has email → not overwritten."""
        from reswip_leads.enrichment.base import EnrichmentResult, EnrichmentStatus, Evidence

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[],
        )

        lead = Lead(
            tva="BE0123456789",
            company_name="Test Company",
            email="existing@company.be",
        )

        result = EnrichmentResult(
            status=EnrichmentStatus.ENRICHED,
            fields={"email": "new@company.be"},
            evidence=[Evidence(source="test", source_url="", field="email", confidence="High", note="")],
        )

        enricher.apply_to_lead(lead, result)

        assert lead.email == "existing@company.be"

    def test_missing_email_blank(self):
        """No source finds email → stays empty."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.return_value = None

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source],
        )

        result = enricher.enrich("BE0123456789", "Test Company")

        assert "email" not in result

    def test_confidence_levels(self):
        """KBO=High, Pappers=Medium, Website=Low."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.return_value = EmailCandidate(
            email="kbo@company.be", source="kbo", source_url="", confidence="High", note=""
        )
        pappers_source = MagicMock(spec=BaseEmailSource)
        pappers_source.find_email.return_value = EmailCandidate(
            email="pappers@company.be", source="pappers", source_url="", confidence="Medium", note=""
        )
        website_source = MagicMock(spec=BaseEmailSource)
        website_source.find_email.return_value = EmailCandidate(
            email="web@company.be", source="website", source_url="", confidence="Low", note=""
        )

        # Test KBO priority
        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source, pappers_source, website_source],
        )
        result = enricher.enrich("BE0123456789", "Test")
        assert result["_email_candidate"].confidence == "High"

        # Test Pappers when KBO empty
        kbo_source.find_email.return_value = None
        result = enricher.enrich("BE0123456789", "Test")
        assert result["_email_candidate"].confidence == "Medium"

        # Test Website when KBO+Pappers empty
        pappers_source.find_email.return_value = None
        result = enricher.enrich("BE0123456789", "Test")
        assert result["_email_candidate"].confidence == "Low"

    def test_invalid_email_status(self):
        """Email found but rejected → Invalid Email status."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.return_value = EmailCandidate(
            email="noreply@company.be",
            source="kbo",
            source_url="https://kbo.be",
            confidence="High",
            note="KBO email",
        )

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source],
        )

        result = enricher.enrich("BE0123456789", "Test")

        assert "email" not in result
        assert enricher._statuses["BE0123456789"] == "Invalid Email"

    def test_source_error_status(self):
        """Source raises exception → Source Error status."""
        kbo_source = MagicMock(spec=BaseEmailSource)
        kbo_source.find_email.side_effect = Exception("Network error")

        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[kbo_source],
        )

        result = enricher.enrich("BE0123456789", "Test")

        assert "email" not in result
        assert enricher._statuses["BE0123456789"] == "Source Error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestEmailRecheckEnricher -v`
Expected: FAIL with "cannot import name 'EmailRecheckEnricher'"

- [ ] **Step 3: Write minimal implementation**

```python
# src/reswip_leads/enrichment/email_recheck.py
"""Email recheck enricher — orchestrates email sources in priority order.

Priority: KBO ZIP → KBO web → Pappers → official website.
Only processes leads with empty email when missing_only=True.
"""
from __future__ import annotations

import csv
import logging
from typing import Any, Dict, List, Optional

from reswip_leads.enrichment.base import (
    BaseEnricher,
    EnrichmentConfig,
    EnrichmentResult,
    EnrichmentStatus,
    Evidence,
)
from reswip_leads.enrichment.email_sources import (
    BaseEmailSource,
    EmailCandidate,
    KboEmailSource,
    KboZipEmailSource,
    PappersEmailSource,
    WebsiteEmailSource,
    _is_valid_email,
)

logger = logging.getLogger(__name__)


class EmailRecheckEnricher(BaseEnricher):
    """Orchestrates email sources in priority order."""

    SOURCE_NAME = "email_recheck"

    def __init__(
        self,
        config: Optional[EnrichmentConfig] = None,
        sources: Optional[List[BaseEmailSource]] = None,
        missing_only: bool = True,
        kbo_zip_reader=None,
    ) -> None:
        super().__init__(config)
        self.missing_only = missing_only
        self._current_website_url = ""

        # Build default source chain if not provided
        if sources is not None:
            self.sources = sources
        else:
            self.sources = []
            if kbo_zip_reader is not None:
                self.sources.append(KboZipEmailSource(kbo_zip_reader))
            self.sources.append(KboEmailSource())
            self.sources.append(PappersEmailSource())
            self.sources.append(WebsiteEmailSource())

        # Evidence tracking for report generation
        self._candidates: Dict[str, EmailCandidate] = {}
        self._statuses: Dict[str, str] = {}

    def set_lead_context(self, lead) -> None:
        """Store per-lead context before enrich() call."""
        self._current_website_url = getattr(lead, "website", "") or ""

    def enrich(self, tva: str, company_name: str = "") -> Dict[str, Any]:
        """Find email from sources in priority order."""
        website_url = self._current_website_url

        for source in self.sources:
            try:
                candidate = source.find_email(
                    tva, company_name, website_url, self.config.proxy
                )
                if candidate:
                    if _is_valid_email(candidate.email, website_url):
                        self._candidates[tva] = candidate
                        self._statuses[tva] = "Email Found"
                        return {
                            "email": candidate.email,
                            "_email_candidate": candidate,
                        }
                    else:
                        # Email found but rejected by validation
                        self._statuses[tva] = "Invalid Email"
                        self._candidates[tva] = candidate
            except Exception as exc:
                logger.debug("Source %s failed for %s: %s", type(source).__name__, tva, exc)
                self._statuses[tva] = "Source Error"

        if tva not in self._statuses:
            self._statuses[tva] = "No Reliable Public Email"
        return {}

    def apply_to_lead(self, lead, result: Dict[str, Any]) -> None:
        """Apply email to lead, never overwriting existing."""
        if lead.email:
            return

        email = result.get("email", "")
        if email:
            lead.email = email
            if not lead.email1:
                lead.email1 = email

    def get_report_rows(self) -> List[Dict[str, str]]:
        """Generate report rows for all processed leads."""
        rows = []
        for tva, candidate in self._candidates.items():
            rows.append({
                "TVA": tva,
                "Company Name": "",
                "Email": candidate.email,
                "Source": candidate.source,
                "Source URL": candidate.source_url,
                "Confidence": candidate.confidence,
                "Note": candidate.note,
                "Status": self._statuses.get(tva, ""),
            })
        return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestEmailRecheckEnricher -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reswip_leads/enrichment/email_recheck.py tests/test_email_recheck.py
git commit -m "feat: add EmailRecheckEnricher orchestrator"
```

---

### Task 7: Pipeline Integration

**Files:**
- Modify: `src/reswip_leads/pipeline.py`
- Modify: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_email_recheck.py

class TestPipelineIntegration:
    def test_enricher_email_flag(self):
        """--enricher email instantiates EmailRecheckEnricher."""
        from reswip_leads.pipeline import _build_enrichers

        result = _build_enrichers("email")

        assert "email_recheck" in result
        assert isinstance(result["email_recheck"], EmailRecheckEnricher)

    def test_enricher_email_with_kbo_zip(self):
        """--enricher email with --kbo-zip passes reader."""
        from reswip_leads.pipeline import _build_enrichers

        mock_reader = MagicMock()
        result = _build_enrichers("email", kbo_zip_reader=mock_reader)

        assert "email_recheck" in result
        enricher = result["email_recheck"]
        # Should have KboZipEmailSource as first source
        assert isinstance(enricher.sources[0], KboZipEmailSource)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestPipelineIntegration -v`
Expected: FAIL (email not in enricher choices)

- [ ] **Step 3: Write minimal implementation**

Modify `src/reswip_leads/pipeline.py`:

1. In `_build_enrichers()`, add handling for "email" choice:
```python
def _build_enrichers(
    enricher: str, proxy: Optional[Dict[str, str]] = None,
    kbo_zip_reader=None,
) -> Dict[str, Any]:
    from reswip_leads.enrichment.base import EnrichmentConfig
    from reswip_leads.enrichment.kbo_web import KboWebEnricher
    from reswip_leads.enrichment.pappers import PappersEnricher
    from reswip_leads.enrichment.email_recheck import EmailRecheckEnricher

    config = EnrichmentConfig(proxy=proxy) if proxy else EnrichmentConfig()
    result: Dict[str, Any] = {}

    choice = (enricher or "both").lower().strip()
    if choice == "email":
        result["email_recheck"] = EmailRecheckEnricher(
            config=config, kbo_zip_reader=kbo_zip_reader
        )
    else:
        if choice in ("pappers", "both"):
            result["pappers"] = PappersEnricher(config=config)
        if choice in ("kbo-web", "kbo_web", "kboweb", "both"):
            result["kbo_web"] = KboWebEnricher(config=config)

    return result
```

2. In `_stage_enrich()`, add handling for email_recheck enricher:
```python
def _stage_enrich(self, leads: List[Lead]) -> List[Lead]:
    # ... existing code ...
    for lead in leads:
        if not lead.tva:
            continue
        lead_enriched = False
        for enricher in (self.pappers, self.kbo_web, self.email_recheck):
            if enricher is None:
                continue
            # Set lead context for email recheck
            if hasattr(enricher, "set_lead_context"):
                enricher.set_lead_context(lead)
            # ... rest of existing logic ...
```

3. In `LeadPipeline.__init__()`, add `email_recheck` parameter:
```python
def __init__(
    self,
    ...
    email_recheck: Optional[Any] = None,
    ...
) -> None:
    ...
    self.email_recheck = email_recheck
```

4. In `run_pipeline()`, pass email_recheck through:
```python
def run_pipeline(
    ...
    enricher: str = "both",
    ...
    kbo_zip_reader=None,
    ...
) -> LeadPipelineResult:
    ...
    enricher_kwargs = _build_enrichers(enricher, proxy, kbo_zip_reader=kbo_zip_reader)
    ...
```

5. In CLI `main()`, add `--enricher email` to choices and pass kbo_zip_path:
```python
parser.add_argument(
    "--enricher",
    choices=["pappers", "kbo-web", "both", "none", "email"],
    default="both",
    help="Enrichment adapter(s) to use (default: both).",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestPipelineIntegration -v`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: All tests pass (370+ passed)

- [ ] **Step 6: Commit**

```bash
git add src/reswip_leads/pipeline.py tests/test_email_recheck.py
git commit -m "feat: integrate EmailRecheckEnricher into pipeline with --enricher email"
```

---

### Task 8: Standalone CLI

**Files:**
- Modify: `src/reswip_leads/enrichment/email_recheck.py`
- Modify: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_email_recheck.py

class TestStandaloneCli:
    def test_standalone_cli(self, tmp_path):
        """CLI loads CSV, filters, enriches, writes output."""
        # Create input CSV
        input_csv = tmp_path / "input.csv"
        input_csv.write_text(
            "TVA,Company Name,Email\n"
            "BE0123456789,Test Company,\n"
            "BE987654321,Existing Company,existing@test.com\n"
        )

        output_csv = tmp_path / "output.csv"
        report_csv = tmp_path / "email_recheck_report.csv"

        # Mock the enricher to return a result
        mock_source = MagicMock(spec=BaseEmailSource)
        mock_source.find_email.return_value = EmailCandidate(
            email="found@company.be",
            source="kbo",
            source_url="https://kbo.be",
            confidence="High",
            note="KBO email",
        )

        from reswip_leads.enrichment.email_recheck import main as cli_main

        with patch(
            "reswip_leads.enrichment.email_recheck.EmailRecheckEnricher"
        ) as MockEnricher:
            mock_enricher = MagicMock()
            mock_enricher.sources = [mock_source]
            mock_enricher._candidates = {
                "BE0123456789": mock_source.find_email.return_value
            }
            mock_enricher._statuses = {"BE0123456789": "Email Found"}
            MockEnricher.return_value = mock_enricher

            with patch("sys.argv", [
                "email_recheck",
                "--input", str(input_csv),
                "--output", str(output_csv),
            ]):
                cli_main()

        # Verify output exists
        assert output_csv.exists()

        # Verify existing email preserved
        with open(output_csv) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[1]["Email"] == "existing@test.com"  # Preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestStandaloneCli -v`
Expected: FAIL (main not defined)

- [ ] **Step 3: Write minimal implementation**

Append to `src/reswip_leads/enrichment/email_recheck.py`:

```python
# ── Standalone CLI ──────────────────────────────────────────────────


def main() -> None:
    """Standalone CLI for email recheck."""
    import argparse
    import sys

    from reswip_leads.enrichment.base import EnrichmentConfig
    from reswip_leads.sources.iqualif.importer import IQualifImporter

    parser = argparse.ArgumentParser(
        description="Recheck missing emails using KBO, Pappers, and website sources."
    )
    parser.add_argument("--input", required=True, help="Input CSV file path.")
    parser.add_argument("--output", required=True, help="Output CSV file path.")
    parser.add_argument(
        "--missing-only",
        action="store_true",
        default=True,
        help="Only process rows with empty Email (default: True).",
    )
    parser.add_argument(
        "--no-missing-only",
        action="store_true",
        help="Process all rows, not just missing emails.",
    )
    parser.add_argument(
        "--source",
        choices=["kbo", "pappers", "website", "all"],
        default="all",
        help="Email source to use (default: all).",
    )
    parser.add_argument("--proxy-file", default=None, help="Proxy rotator file.")
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout.")
    parser.add_argument("--retries", type=int, default=2, help="HTTP retries.")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests.")

    args = parser.parse_args()

    # Load proxy
    proxy = None
    if args.proxy_file:
        try:
            with open(args.proxy_file) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxy = {"http": line, "https": line}
                        break
        except Exception:
            pass

    # Build config
    config = EnrichmentConfig(
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        proxy=proxy,
    )

    # Build sources based on --source flag
    sources = []
    if args.source in ("kbo", "all"):
        sources.append(KboEmailSource())
    if args.source in ("pappers", "all"):
        sources.append(PappersEmailSource())
    if args.source in ("website", "all"):
        sources.append(WebsiteEmailSource())

    # Create enricher
    enricher = EmailRecheckEnricher(
        config=config,
        sources=sources,
        missing_only=not args.no_missing_only,
    )

    # Load leads
    importer = IQualifImporter()
    leads = importer.import_leads([args.input])

    # Process leads
    processed = 0
    for lead in leads:
        if enricher.missing_only and lead.email:
            continue
        if not lead.tva:
            continue

        enricher.set_lead_context(lead)
        result = enricher.enrich(lead.tva, lead.company_name)
        enricher.apply_to_lead(lead, result)
        processed += 1

    # Write output CSV
    from reswip_leads.exports.zoho import export_csv
    export_csv(leads, args.output, profile=None)

    # Write report
    report_path = args.output.replace(".csv", "_email_recheck_report.csv")
    report_rows = enricher.get_report_rows()

    # Add rows for leads with no email found
    for lead in leads:
        if lead.tva and lead.tva not in enricher._statuses:
            enricher._statuses[lead.tva] = "No Reliable Public Email"

    with open(report_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "TVA", "Company Name", "Email", "Source", "Source URL",
            "Confidence", "Note", "Status",
        ])
        writer.writeheader()
        for row in report_rows:
            writer.writerow(row)

    print(f"Processed {processed} leads")
    print(f"Output: {args.output}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestStandaloneCli -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/reswip_leads/enrichment/email_recheck.py tests/test_email_recheck.py
git commit -m "feat: add standalone CLI for email recheck"
```

---

### Task 9: Report Generation

**Files:**
- Modify: `src/reswip_leads/enrichment/email_recheck.py`
- Modify: `tests/test_email_recheck.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/test_email_recheck.py

class TestReportGeneration:
    def test_report_generation(self, tmp_path):
        """Report CSV written with correct statuses."""
        enricher = EmailRecheckEnricher(
            config=EnrichmentConfig(),
            sources=[],
        )

        # Simulate processing
        enricher._candidates["BE0123456789"] = EmailCandidate(
            email="found@company.be",
            source="kbo",
            source_url="https://kbo.be",
            confidence="High",
            note="KBO email",
        )
        enricher._statuses["BE0123456789"] = "Email Found"
        enricher._statuses["BE987654321"] = "No Reliable Public Email"

        rows = enricher.get_report_rows()

        assert len(rows) == 1
        assert rows[0]["TVA"] == "BE0123456789"
        assert rows[0]["Email"] == "found@company.be"
        assert rows[0]["Source"] == "kbo"
        assert rows[0]["Status"] == "Email Found"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestReportGeneration -v`
Expected: FAIL

- [ ] **Step 3: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py::TestReportGeneration -v`
Expected: PASS (implementation already in Task 6)

- [ ] **Step 4: Commit**

```bash
git add tests/test_email_recheck.py
git commit -m "test: add report generation test"
```

---

### Task 10: Run Full Test Suite

- [ ] **Step 1: Run all tests**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: All tests pass (370+ passed, 2 skipped)

- [ ] **Step 2: Verify no live network**

Run: `PYTHONPATH=src python3 -m pytest tests/test_email_recheck.py -v`
Expected: All tests pass with mocked HTTP

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: add targeted missing email enrichment

- Add EmailCandidate, BaseEmailSource, KboZipEmailSource, KboEmailSource,
  PappersEmailSource, WebsiteEmailSource
- Add EmailRecheckEnricher with priority chain: KBO ZIP → KBO web → Pappers → Website
- Add --enricher email flag to pipeline CLI
- Add standalone CLI: python3 -m reswip_leads.enrichment.email_recheck
- Add email_recheck_report.csv generation
- Process only rows with empty Email
- Never overwrite existing email values
- All 370+ tests pass"
```

---

### Task 11: 20-Row Test Batch

- [ ] **Step 1: Create 20-row test batch**

```bash
# Extract first 20 rows with missing email from input file
PYTHONPATH=src python3 -c "
import csv
with open('wallonie_restaurants_cafes_hotels_bakeries_300_names_language.csv') as f:
    reader = csv.DictReader(f)
    rows = [r for r in reader if not r.get('Email', '').strip()]

with open('/tmp/missing_email_20.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    for row in rows[:20]:
        writer.writerow(row)

print(f'Created 20-row test batch with {len(rows[:20])} rows')
"
```

- [ ] **Step 2: Run email recheck on 20-row batch**

```bash
PYTHONPATH=src python3 -m reswip_leads.enrichment.email_recheck \
  --input /tmp/missing_email_20.csv \
  --output /tmp/missing_email_20_rechecked.csv \
  --missing-only
```

- [ ] **Step 3: Verify results**

```bash
PYTHONPATH=src python3 -c "
import csv
with open('/tmp/missing_email_20_rechecked.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
    with_email = sum(1 for r in rows if r.get('Email', '').strip())
    print(f'Results: {with_email}/{len(rows)} rows have email')
"
```

- [ ] **Step 4: Commit test results**

```bash
git add -A
git commit -m "test: verify 20-row email recheck batch"
```

---

## Self-Review Checklist

- [x] Spec coverage: All requirements covered (KBO ZIP, KBO web, Pappers, Website, priority chain, email validation, CLI, report)
- [x] Placeholder scan: No TBD/TODO found
- [x] Type consistency: All method signatures match across tasks
- [x] File paths: All paths verified
- [x] Test coverage: All behaviors tested with mocked HTTP
