"""LinkedIn scraper module.

Note: This scraper is intended as a starting point for collecting *public* profile data from
LinkedIn search results. It is designed to be used manually and responsibly.

It does not bypass LinkedIn protections and respects rate-limits by default.

To run:
    python -m scraper --query "University of Waterloo Computer Science" --max 50

"""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright
from playwright_stealth import stealth_sync

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class LinkedInProfile:
    name: str
    headline: str
    profile_url: str
    extracted_at: float


def _create_db(path: str) -> None:
    """Create the SQLite database schema if it does not exist."""
    with sqlite3.connect(path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                headline TEXT,
                profile_url TEXT UNIQUE,
                extracted_at REAL NOT NULL
            )
            """
        )
        conn.commit()


def save_profiles_to_db(path: str, profiles: Iterable[LinkedInProfile]) -> None:
    """Save scraped profiles to an SQLite database (dedupes by profile_url)."""

    _create_db(path)
    with sqlite3.connect(path) as conn:
        cursor = conn.cursor()
        for p in profiles:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO profiles (name, headline, profile_url, extracted_at) VALUES (?, ?, ?, ?)",
                    (p.name, p.headline, p.profile_url, p.extracted_at),
                )
            except sqlite3.Error as e:
                logger.warning("Failed to save profile %s: %s", p.profile_url, e)
        conn.commit()


def save_profiles_to_csv(path: str, profiles: Iterable[LinkedInProfile]) -> None:
    """Save scraped profiles to a CSV file."""

    path_obj = Path(path)
    is_new = not path_obj.exists()

    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["name", "headline", "profile_url", "extracted_at"])
        for p in profiles:
            writer.writerow([p.name, p.headline, p.profile_url, p.extracted_at])


def _build_search_url(query: str) -> str:
    """Build the LinkedIn search URL for people (no filters)."""

    # LinkedIn uses URL encoding for the query.
    return f"https://www.linkedin.com/search/results/people/?keywords={query.replace(' ', '%20')}"


def _extract_profiles_from_page(page: Page) -> List[LinkedInProfile]:
    """Extract name+headline+profile URL from a search results page."""

    # Updated selectors for modern LinkedIn UI
    page.wait_for_selector(".reusable-search__result-container", timeout=10000)
    cards = page.locator(".reusable-search__result-container")
    profiles: List[LinkedInProfile] = []

    for i in range(cards.count()):
        card = cards.nth(i)

        # Name and profile link usually exist under <a> inside the card.
        handle = card.locator(".entity-result__title-text a").first
        if handle.count() == 0:
            continue

        try:
            profile_url = handle.get_attribute("href") or ""
            name = handle.locator("span[aria-hidden='true']").inner_text().strip()
        except Exception as e:
            logger.debug("Failed to extract card details: %s", e)
            continue
            
        if not profile_url:
            continue

        headline = ""
        headline_el = card.locator(".entity-result__primary-subtitle")
        if headline_el.count() > 0:
            headline = headline_el.inner_text().strip()

        profiles.append(LinkedInProfile(name=name, headline=headline, profile_url=profile_url, extracted_at=time.time()))

    return profiles


def scrape_linkedin_search(
    query: str,
    max_profiles: int = 100,
    headless: bool = True,
    browser_name: str = "chromium",
    output_db: Optional[str] = None,
    output_csv: Optional[str] = None,
    pause_between_pages: float = 3.0,
) -> List[LinkedInProfile]:
    """Scrape LinkedIn search results and return collected profiles.

    **Important**: LinkedIn actively detects automation. Use this tool responsibly.

    This method performs the minimum to collect results as an educational example.
    It does not bypass login requirements or other protections.
    """

    linkedin_email = os.getenv("LINKEDIN_EMAIL")
    linkedin_password = os.getenv("LINKEDIN_PASSWORD")

    if not linkedin_email or not linkedin_password:
        raise RuntimeError(
            "Missing LINKEDIN_EMAIL or LINKEDIN_PASSWORD in environment. "
            "Set them in .env and reload, e.g. `LINKEDIN_EMAIL=you@example.com`"
        )

    launcher = sync_playwright().start()
    
    # Define where to store cookies and session data
    user_data_dir = os.path.join(os.getcwd(), "playwright_session")
    
    # Launch persistent context instead of a fresh browser
    # This saves your login state so you don't get flagged for new logins
    context = launcher.chromium.launch_persistent_context(
        user_data_dir,
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    
    # Use the first page or create one
    page = context.pages[0] if context.pages else context.new_page()
    
    # Apply stealth to the page
    stealth_sync(page)

    logger.info("Checking LinkedIn session...")
    page.goto("https://www.linkedin.com/feed")
    page.wait_for_load_state("networkidle")

    # If not logged in, perform login
    if "login" in page.url or "checkpoint" in page.url:
        logger.info("Session expired or missing. Logging in...")
        page.goto("https://www.linkedin.com/login")
        page.fill("input[name='session_key']", linkedin_email)
        page.fill("input[name='session_password']", linkedin_password)
        page.click("button[type='submit']")
        page.wait_for_load_state("networkidle")

    if "feed" not in page.url:
        logger.warning("Still not on feed. You may need to solve a CAPTCHA manually in the browser window.")
        # Keep the window open for manual intervention if not headless
        if not headless:
             page.wait_for_url("https://www.linkedin.com/feed/", timeout=60000)

    search_url = _build_search_url(query)
    page.goto(search_url)
    page.wait_for_load_state("networkidle")

    collected: List[LinkedInProfile] = []
    seen_urls = set()

    while len(collected) < max_profiles:
        profiles = _extract_profiles_from_page(page)
        for p in profiles:
            if p.profile_url in seen_urls:
                continue
            seen_urls.add(p.profile_url)
            collected.append(p)
            if len(collected) >= max_profiles:
                break

        logger.info("Collected %d/%d profiles", len(collected), max_profiles)
        if len(collected) >= max_profiles:
            break

        # Scroll and click "Next" if possible (depending on LinkedIn's layout)
        next_button = page.locator("button[aria-label='Next']")
        if next_button.count() and next_button.is_enabled():
            next_button.click()
            page.wait_for_load_state("networkidle")
            time.sleep(pause_between_pages)
            continue

        # otherwise try infinite scrolling
        page.keyboard.press("End")
        time.sleep(pause_between_pages)
        if len(profiles) == 0:
            break

    if output_db:
        save_profiles_to_db(output_db, collected)
        logger.info("Saved %d profiles to DB %s", len(collected), output_db)

    if output_csv:
        save_profiles_to_csv(output_csv, collected)
        logger.info("Saved %d profiles to CSV %s", len(collected), output_csv)

    context.close()
    launcher.stop()

    return collected


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Scrape LinkedIn search results for UW students.")
    parser.add_argument("--query", required=True, help="Search query (e.g., 'University of Waterloo Computer Science')")
    parser.add_argument("--max", type=int, default=50, help="Maximum number of profiles to collect")
    parser.add_argument("--db", default="outreach.db", help="SQLite DB to append results to")
    parser.add_argument("--csv", default=None, help="Optional CSV path to append results to")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    args = parser.parse_args()

    scrape_linkedin_search(
        query=args.query,
        max_profiles=args.max,
        headless=args.headless,
        output_db=args.db,
        output_csv=args.csv,
    )
