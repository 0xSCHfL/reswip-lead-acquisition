"""Open Infobel in headed mode so you can solve the Cloudflare captcha manually.
The browser stays open for 120s — solve the captcha, then close the browser.
"""
import time
from pathlib import Path

profile_dir = Path("~/.infobel-scrape-profile").expanduser()
profile_dir.mkdir(parents=True, exist_ok=True)

from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        viewport={"width": 1280, "height": 900},
        locale="fr-BE",
        timezone_id="Europe/Brussels",
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.infobel.com/fr/belgium", wait_until="domcontentloaded")
    print(">>> Browser opened. Solve the Cloudflare captcha manually.")
    print(">>> Close the browser window when done. Waiting 120s...")
    page.wait_for_timeout(120_000)
    context.close()
