import logging
logging.basicConfig(level=logging.DEBUG, force=True)
from playwright.sync_api import sync_playwright
from pathlib import Path

resolved_profile = Path("/root/.infobel-scrape-profile")
resolved_profile.mkdir(parents=True, exist_ok=True)

with sync_playwright() as pw:
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(resolved_profile),
        headless=False,
        executable_path="/usr/bin/chromium",
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"],
        viewport={"width": 1280, "height": 900},
        locale="fr-BE",
        timezone_id="Europe/Brussels",
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.goto("https://www.infobel.com/fr/belgium/rocourt/liege/BE106476086-042630168/businessdetails.aspx", wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(5_000)

    r1 = page.evaluate("""() => {
        const el = document.querySelector("#__abconsent-cmp");
        return el ? el.style.display + " | " + el.offsetParent : "no consent";
    }""")
    print("Before dismiss:", r1)

    page.evaluate("""() => {
        const el = document.querySelector("#__abconsent-cmp");
        if (el) el.style.display = "none";
    }""")
    page.wait_for_timeout(1_000)

    r2 = page.evaluate("""() => {
        const els = document.querySelectorAll("[class*=detail-text]");
        return Array.from(els).filter(e => e.textContent.trim() === "Afficher le téléphone").map(e => e.tagName + " | " + e.className + " | visible:" + (e.offsetParent !== null)).join(" || ") || "no button found";
    }""")
    print("Button info:", r2)

    page.evaluate("""() => {
        const els = document.querySelectorAll("[class*=detail-text]");
        for (const el of els) {
            if (el.textContent.trim() === "Afficher le téléphone") {
                el.click(); break;
            }
        }
    }""")
    page.wait_for_timeout(3_000)

    r3 = page.evaluate("""() => {
        const sections = document.querySelectorAll("[id^=phones-region]");
        if (sections.length > 0) {
            return sections[0].innerHTML.substring(0, 500);
        }
        return "no phones section";
    }""")
    print("After click:", r3)

    context.close()
