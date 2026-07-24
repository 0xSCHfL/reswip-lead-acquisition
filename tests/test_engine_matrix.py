"""Browser-engine matrix test for Infobel /Landing/Abuse redirect.

Runs 4 conditions (Chromium, Chromium+stealth, Firefox, WebKit) with fresh
profiles, records token + final URL + detail-link count for each.
"""
from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

RESULTS: list[dict] = []
SEARCH_TERM = "Restaurant"
LOCATION = "Chastre"
INFOBEL_HOME = "https://www.infobel.com/fr/belgium/"
TIMEOUT_MS = 60_000


def _token_summary(token: str | None) -> str:
    if not token:
        return "NONE"
    h = hashlib.sha256(token.encode()).hexdigest()[:12]
    return f"len={len(token)} prefix={token[:12]}… sha256={h}"


def _run_condition(name: str, profile_dir: Path, **launch_kw):
    """Run one matrix condition. Returns a result dict."""
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    result = {
        "condition": name,
        "token": "N/A",
        "final_url": "N/A",
        "abuse_redirect": False,
        "detail_links": 0,
        "elapsed_s": 0,
        "error": None,
    }

    t0 = time.monotonic()
    try:
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                **launch_kw,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                print(f"  [{name}] navigating to {INFOBEL_HOME}")
                page.goto(INFOBEL_HOME, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                page.wait_for_timeout(3_000)
                print(f"  [{name}] page title: {page.title()!r}")

                # Fill search term
                term = page.locator("#search-term-input-header")
                if term.count():
                    term.last.click()
                    page.wait_for_timeout(300)
                    term.last.type(SEARCH_TERM, delay=80)
                    page.wait_for_timeout(2_000)
                    # Try Kendo dropdown
                    try:
                        items = page.locator("#search-term-input-header_listbox .k-item")
                        if items.count():
                            items.first.click(timeout=3_000)
                            page.wait_for_timeout(500)
                    except Exception:
                        pass
                    print(f"  [{name}] search term filled")
                else:
                    print(f"  [{name}] WARNING: search term input not found")
                    result["error"] = "search term input not found"
                    return result

                # Fill location
                place = page.locator("#search-location-input-header")
                if place.count():
                    place.last.click()
                    page.wait_for_timeout(300)
                    place.last.type(LOCATION, delay=80)
                    page.wait_for_timeout(2_000)
                    try:
                        containers = page.locator(".k-list-container")
                        for i in range(containers.count()):
                            c = containers.nth(i)
                            if c.evaluate("el => el.offsetParent !== null"):
                                ki = c.locator(".k-item")
                                if ki.count():
                                    ki.first.click(timeout=3_000)
                                    page.wait_for_timeout(500)
                                    break
                    except Exception:
                        pass
                    print(f"  [{name}] location filled")
                else:
                    print(f"  [{name}] WARNING: location input not found")

                # Extract token via AJAX
                print(f"  [{name}] extracting token...")
                token = page.evaluate(
                    """() => {
                        return new Promise((resolve) => {
                            var $form = jQuery('#search-form-header');
                            jQuery.ajax({
                                url: '/fr/belgium/Search/EncodeSearchCriteria',
                                data: $form.serialize(),
                                type: 'POST',
                                success: function(data) {
                                    resolve(data.success ? data.token : null);
                                },
                                error: function() { resolve(null); }
                            });
                            setTimeout(() => resolve(null), 15_000);
                        });
                    }"""
                )
                result["token"] = _token_summary(token)
                if not token:
                    result["error"] = "no token returned"
                    print(f"  [{name}] no token — aborting")
                    return result

                results_url = (
                    f"https://www.infobel.com/fr/belgium/Search"
                    f"/BusinessResults?token={token}"
                )
                print(f"  [{name}] navigating to results...")

                page.goto(results_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                page.wait_for_timeout(3_000)

                final_url = page.url
                result["final_url"] = final_url
                result["abuse_redirect"] = "/Landing/Abuse" in final_url

                if result["abuse_redirect"]:
                    print(f"  [{name}] ABUSE REDIRECT — {final_url}")
                else:
                    detail_count = page.locator('a[href*="businessdetails"]').count()
                    result["detail_links"] = detail_count
                    print(f"  [{name}] OK — {detail_count} detail links")

            finally:
                ctx.close()
    except Exception as exc:
        result["error"] = str(exc)[:200]
        print(f"  [{name}] ERROR: {str(exc)[:120]}")
    finally:
        result["elapsed_s"] = round(time.monotonic() - t0, 1)

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Infobel engine matrix test")
    parser.add_argument("--keep-profiles", action="store_true", help="Don't delete temp profiles")
    args = parser.parse_args()

    conditions = [
        ("Chromium (no stealth)", {
            "channel": "chromium",
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        }),
        ("Chromium (stealth)", {
            "channel": "chromium",
            "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            "stealth": True,
        }),
        ("Firefox", {
            "firefox_user_prefs": {
                "dom.webdriver.enabled": False,
                "useAutomationExtension": False,
            },
        }),
    ]

    print("=" * 70)
    print("INFOBEL ENGINE MATRIX TEST")
    print(f"search={SEARCH_TERM!r}  location={LOCATION!r}  timeout={TIMEOUT_MS}ms")
    print("=" * 70)

    profile_dirs: list[Path] = []

    for name, kw in conditions:
        stealth = kw.pop("stealth", False)
        profile = Path(f"/tmp/infobel-matrix-{name.split()[0].lower()}")
        profile_dirs.append(profile)
        # Clean profile before run
        if profile.exists():
            shutil.rmtree(profile, ignore_errors=True)

        print(f"\n--- {name} ---")

        if stealth:
            # Monkey-patch launch to apply stealth after context creation
            original_launch = None
            from playwright.sync_api import sync_playwright
            # We'll apply stealth inside _run_condition by patching
            # Actually, simpler: apply stealth in the condition runner
            _apply_stealth = True
        else:
            _apply_stealth = False

        # For stealth, we need a wrapper
        if stealth:
            def _stealth_runner(nm, pd, **lkw):
                from playwright.sync_api import sync_playwright
                from playwright_stealth import Stealth
                pd.mkdir(parents=True, exist_ok=True)
                result = {
                    "condition": nm,
                    "token": "N/A",
                    "final_url": "N/A",
                    "abuse_redirect": False,
                    "detail_links": 0,
                    "elapsed_s": 0,
                    "error": None,
                }
                t0 = time.monotonic()
                try:
                    with sync_playwright() as pw:
                        ctx = pw.chromium.launch_persistent_context(
                            user_data_dir=str(pd), headless=True, **lkw,
                        )
                        try:
                            Stealth().apply_stealth_sync(ctx)
                            print(f"  [{nm}] stealth applied")
                            page = ctx.pages[0] if ctx.pages else ctx.new_page()
                            print(f"  [{nm}] navigating to {INFOBEL_HOME}")
                            page.goto(INFOBEL_HOME, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                            page.wait_for_timeout(3_000)
                            print(f"  [{nm}] page title: {page.title()!r}")

                            term = page.locator("#search-term-input-header")
                            if term.count():
                                term.last.click()
                                page.wait_for_timeout(300)
                                term.last.type(SEARCH_TERM, delay=80)
                                page.wait_for_timeout(2_000)
                                try:
                                    items = page.locator("#search-term-input-header_listbox .k-item")
                                    if items.count():
                                        items.first.click(timeout=3_000)
                                        page.wait_for_timeout(500)
                                except Exception:
                                    pass
                                print(f"  [{nm}] search term filled")
                            else:
                                result["error"] = "search term input not found"
                                return result

                            place = page.locator("#search-location-input-header")
                            if place.count():
                                place.last.click()
                                page.wait_for_timeout(300)
                                place.last.type(LOCATION, delay=80)
                                page.wait_for_timeout(2_000)
                                try:
                                    containers = page.locator(".k-list-container")
                                    for i in range(containers.count()):
                                        c = containers.nth(i)
                                        if c.evaluate("el => el.offsetParent !== null"):
                                            ki = c.locator(".k-item")
                                            if ki.count():
                                                ki.first.click(timeout=3_000)
                                                page.wait_for_timeout(500)
                                                break
                                except Exception:
                                    pass
                                print(f"  [{nm}] location filled")

                            print(f"  [{nm}] extracting token...")
                            token = page.evaluate(
                                """() => {
                                    return new Promise((resolve) => {
                                        var $form = jQuery('#search-form-header');
                                        jQuery.ajax({
                                            url: '/fr/belgium/Search/EncodeSearchCriteria',
                                            data: $form.serialize(),
                                            type: 'POST',
                                            success: function(data) {
                                                resolve(data.success ? data.token : null);
                                            },
                                            error: function() { resolve(null); }
                                        });
                                        setTimeout(() => resolve(null), 15_000);
                                    });
                                }"""
                            )
                            result["token"] = _token_summary(token)
                            if not token:
                                result["error"] = "no token returned"
                                print(f"  [{nm}] no token — aborting")
                                return result

                            results_url = (
                                f"https://www.infobel.com/fr/belgium/Search"
                                f"/BusinessResults?token={token}"
                            )
                            print(f"  [{nm}] navigating to results...")
                            page.goto(results_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                            page.wait_for_timeout(3_000)

                            final_url = page.url
                            result["final_url"] = final_url
                            result["abuse_redirect"] = "/Landing/Abuse" in final_url

                            if result["abuse_redirect"]:
                                print(f"  [{nm}] ABUSE REDIRECT — {final_url}")
                            else:
                                detail_count = page.locator('a[href*="businessdetails"]').count()
                                result["detail_links"] = detail_count
                                print(f"  [{nm}] OK — {detail_count} detail links")

                        finally:
                            ctx.close()
                except Exception as exc:
                    result["error"] = str(exc)[:200]
                    print(f"  [{nm}] ERROR: {str(exc)[:120]}")
                finally:
                    result["elapsed_s"] = round(time.monotonic() - t0, 1)
                return result

            r = _stealth_runner(name, profile, **kw)
        else:
            r = _run_condition(name, profile, **kw)
        RESULTS.append(r)

    # Also try WebKit
    print(f"\n--- WebKit ---")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.webkit.launch(headless=True)
            b.close()
        webkit_available = True
    except Exception as e:
        webkit_available = False
        print(f"  WebKit: UNAVAILABLE — {str(e)[:100]}")

    if webkit_available:
        profile = Path("/tmp/infobel-matrix-webkit")
        profile_dirs.append(profile)
        if profile.exists():
            shutil.rmtree(profile, ignore_errors=True)
        # WebKit needs different launch — can't use launch_persistent_context with chromium kwargs
        def _webkit_runner(pd):
            from playwright.sync_api import sync_playwright
            pd.mkdir(parents=True, exist_ok=True)
            result = {
                "condition": "WebKit",
                "token": "N/A",
                "final_url": "N/A",
                "abuse_redirect": False,
                "detail_links": 0,
                "elapsed_s": 0,
                "error": None,
            }
            t0 = time.monotonic()
            try:
                with sync_playwright() as pw:
                    ctx = pw.webkit.launch_persistent_context(
                        user_data_dir=str(pd), headless=True,
                    )
                    try:
                        page = ctx.pages[0] if ctx.pages else ctx.new_page()
                        print(f"  [WebKit] navigating to {INFOBEL_HOME}")
                        page.goto(INFOBEL_HOME, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                        page.wait_for_timeout(3_000)
                        print(f"  [WebKit] page title: {page.title()!r}")

                        term = page.locator("#search-term-input-header")
                        if term.count():
                            term.last.click()
                            page.wait_for_timeout(300)
                            term.last.type(SEARCH_TERM, delay=80)
                            page.wait_for_timeout(2_000)
                            try:
                                items = page.locator("#search-term-input-header_listbox .k-item")
                                if items.count():
                                    items.first.click(timeout=3_000)
                                    page.wait_for_timeout(500)
                            except Exception:
                                pass
                            print(f"  [WebKit] search term filled")
                        else:
                            result["error"] = "search term input not found"
                            return result

                        place = page.locator("#search-location-input-header")
                        if place.count():
                            place.last.click()
                            page.wait_for_timeout(300)
                            place.last.type(LOCATION, delay=80)
                            page.wait_for_timeout(2_000)
                            try:
                                containers = page.locator(".k-list-container")
                                for i in range(containers.count()):
                                    c = containers.nth(i)
                                    if c.evaluate("el => el.offsetParent !== null"):
                                        ki = c.locator(".k-item")
                                        if ki.count():
                                            ki.first.click(timeout=3_000)
                                            page.wait_for_timeout(500)
                                            break
                            except Exception:
                                pass
                            print(f"  [WebKit] location filled")

                        print(f"  [WebKit] extracting token...")
                        token = page.evaluate(
                            """() => {
                                return new Promise((resolve) => {
                                    var $form = jQuery('#search-form-header');
                                    jQuery.ajax({
                                        url: '/fr/belgium/Search/EncodeSearchCriteria',
                                        data: $form.serialize(),
                                        type: 'POST',
                                        success: function(data) {
                                            resolve(data.success ? data.token : null);
                                        },
                                        error: function() { resolve(null); }
                                    });
                                    setTimeout(() => resolve(null), 15_000);
                                });
                            }"""
                        )
                        result["token"] = _token_summary(token)
                        if not token:
                            result["error"] = "no token returned"
                            print(f"  [WebKit] no token — aborting")
                            return result

                        results_url = (
                            f"https://www.infobel.com/fr/belgium/Search"
                            f"/BusinessResults?token={token}"
                        )
                        print(f"  [WebKit] navigating to results...")
                        page.goto(results_url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                        page.wait_for_timeout(3_000)

                        final_url = page.url
                        result["final_url"] = final_url
                        result["abuse_redirect"] = "/Landing/Abuse" in final_url

                        if result["abuse_redirect"]:
                            print(f"  [WebKit] ABUSE REDIRECT — {final_url}")
                        else:
                            detail_count = page.locator('a[href*="businessdetails"]').count()
                            result["detail_links"] = detail_count
                            print(f"  [WebKit] OK — {detail_count} detail links")

                    finally:
                        ctx.close()
            except Exception as exc:
                result["error"] = str(exc)[:200]
                print(f"  [WebKit] ERROR: {str(exc)[:120]}")
            finally:
                result["elapsed_s"] = round(time.monotonic() - t0, 1)
            return result

        r = _webkit_runner(profile)
        RESULTS.append(r)
    else:
        RESULTS.append({
            "condition": "WebKit",
            "token": "N/A",
            "final_url": "N/A",
            "abuse_redirect": False,
            "detail_links": 0,
            "elapsed_s": 0,
            "error": "UNAVAILABLE — missing system deps",
        })

    # Print summary table
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)
    header = f"{'Condition':<28} {'Token':<32} {'Abuse':<7} {'Links':<7} {'Time':<7} {'Error'}"
    print(header)
    print("-" * len(header))
    for r in RESULTS:
        abuse = "YES" if r["abuse_redirect"] else "no"
        links = str(r["detail_links"]) if not r["abuse_redirect"] else "0"
        err = r["error"] or ""
        print(
            f"{r['condition']:<28} {r['token']:<32} {abuse:<7} {links:<7} {r['elapsed_s']:<7} {err}"
        )

    print("\nCONCLUSION:")
    abuse_count = sum(1 for r in RESULTS if r["abuse_redirect"])
    if abuse_count == len(RESULTS):
        print("ALL engines hit /Landing/Abuse → block is server/session/IP/token-based.")
        print("Changing browser fingerprint will NOT help. Need alternative strategy.")
    elif abuse_count == 0:
        print("No abuse redirects — all engines work!")
    else:
        print(f"{abuse_count}/{len(RESULTS)} engines hit abuse — fingerprint matters for some.")

    # Cleanup
    if not args.keep_profiles:
        for pd in profile_dirs:
            shutil.rmtree(pd, ignore_errors=True)
        print("\nTemp profiles cleaned up.")


if __name__ == "__main__":
    main()
