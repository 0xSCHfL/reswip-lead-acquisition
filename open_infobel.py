"""Just open Infobel in a headed browser — solve captcha, then close when done."""
from pathlib import Path
from playwright.sync_api import sync_playwright

profile_dir = Path("~/.infobel-scrape-profile").expanduser()
profile_dir.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
        viewport={"width": 1280, "height": 900},
        locale="fr-BE",
        timezone_id="Europe/Brussels",
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.infobel.com/fr/belgium/memo_2000/liege/BE100499007-042250814/businessdetails.aspx")
    print("\n=== Browser is OPEN. Solve the Cloudflare captcha in the window. ===")
    print("=== Close the browser window when done (or wait 120s). ===")
    for i in range(120):
        import time
        time.sleep(1)
        try:
            if not context.pages or context.pages[0].is_closed():
                break
        except:
            break
    context.close()
