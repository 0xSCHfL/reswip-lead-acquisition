import re, time, sys
from playwright.sync_api import sync_playwright

url = "https://www.infobel.com/fr/belgium/kfc_rocourt/liege/BE106375786-043319359/businessdetails.aspx"
profile = "/root/.infobel-scrape-profile"

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        profile, headless=False,
        executable_path="/usr/bin/chromium",
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        viewport={"width": 1280, "height": 900}, locale="fr-BE"
    )
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    cmp = page.locator("#__abconsent-cmp")
    if cmp.is_visible():
        page.evaluate("document.querySelector('#__abconsent-cmp').style.display='none'")
        page.wait_for_timeout(500)

    # Use JS to hide consent + click email button (like phone fix)
    page.evaluate("""() => {
        const cmp = document.querySelector('#__abconsent-cmp');
        if (cmp) cmp.style.display = 'none';
        const els = document.querySelectorAll('[class*="detail-text"]');
        for (const el of els) {
            if (el.textContent.trim() === 'Envoyer un e-mail') {
                el.click(); break;
            }
        }
    }""")
    page.wait_for_timeout(3000)
    print("--- After JS click ---")
    print("URL:", page.url)
    body = page.locator("body").inner_text()
    print("Body snippet:", body[:2000])
    emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.]+', body)
    print("Emails found:", emails)
    modals = page.locator("[role=dialog], .modal, .popup, [class*=overlay]")
    print("Modal count:", modals.count())
    if modals.count():
        print("Modal HTML:", modals.first.evaluate("el => el.outerHTML")[:500])
    # Check for new windows/tabs
    print("Pages:", [p.url for p in ctx.pages])

    ctx.close()
