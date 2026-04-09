"""One-time interactive login to NZ Herald.

Opens a visible browser window for you to log in manually.
Saves session cookies to pipeline/.herald_cookies.json for use by the scraper.

Usage:
    python -m pipeline.login_herald
"""

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

COOKIE_FILE = Path(__file__).parent / ".herald_cookies.json"
LOGIN_URL = "https://www.nzherald.co.nz/my-account/login/"
VERIFY_URL = "https://www.nzherald.co.nz/my-account/"


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return

    print("Opening NZ Herald login page...")
    print("Please log in with your premium account credentials.")
    print()
    print("Once you are fully logged in, press ENTER here in the terminal.")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # Wait for user to press Enter — they control when they're done
        await asyncio.get_event_loop().run_in_executor(None, input, "Press ENTER after you have logged in... ")

        # Navigate to account page to verify login worked
        print("\nVerifying login...")
        await page.goto(VERIFY_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        body = await page.inner_text("body")
        body_lower = body[:1000].lower()

        # Check for logged-in vs logged-out indicators
        is_logged_out = "sign in" in body_lower and "subscribe" in body_lower
        is_logged_in = any(x in body_lower for x in ["my account", "my profile", "log out", "sign out", "manage", "subscription"])

        if is_logged_out and not is_logged_in:
            print("\nERROR: Login not detected. The page still shows 'Sign In'.")
            print("Please try again and make sure you complete the login process.")
            await browser.close()
            return

        # Save all cookies
        cookies = await context.cookies()
        herald_cookies = [c for c in cookies if "nzherald" in c.get("domain", "")]

        # Verify we have auth-related cookies
        auth_names = [c["name"] for c in herald_cookies if any(x in c["name"].lower() for x in ["session", "auth", "token", "piano", "__tp", "user"])]

        COOKIE_FILE.write_text(json.dumps(herald_cookies, indent=2))
        print(f"\nSaved {len(herald_cookies)} cookies to {COOKIE_FILE}")
        if auth_names:
            print(f"Auth cookies found: {', '.join(auth_names)}")

        # Test by loading a premium article
        print("\nTesting premium access...")
        test_url = "https://www.nzherald.co.nz/nz/politics/"
        await page.goto(test_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        body = await page.inner_text("body")

        if "sign in" not in body[:500].lower():
            print("Premium access confirmed.")
        else:
            print("WARNING: Could not confirm premium access. You may need to log in again.")

        await browser.close()

    print(f"\nDone. Cookie file: {COOKIE_FILE}")
    print("To refresh cookies, run this script again.")


if __name__ == "__main__":
    asyncio.run(main())
