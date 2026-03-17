"""Main entry point for the outreach toolkit.

This script provides a small CLI to run scraping, message generation, and outreach workflows.

Usage examples:
    python main.py scrape --query "University of Waterloo Computer Science" --max 50
    python main.py generate --db outreach.db --max 25
    python main.py outreach --db outreach.db --dry-run
"""

from __future__ import annotations

import argparse
import os

from scraper import scrape_linkedin_search
from outreach import enqueue_messages, load_profiles, send_outreach


def main() -> None:
    parser = argparse.ArgumentParser(description="Electrium Mobility Outreach Toolkit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Scrape subcommand
    scrape_parser = subparsers.add_parser("scrape", help="Scrape LinkedIn search results")
    scrape_parser.add_argument("--query", required=True, help="LinkedIn search query")
    scrape_parser.add_argument("--max", type=int, default=50, help="Max number of profiles to collect")
    scrape_parser.add_argument("--db", default="outreach.db", help="SQLite database path")
    scrape_parser.add_argument("--csv", default=None, help="Optional CSV file path to save results")
    scrape_parser.add_argument("--headless", action="store_true", help="Run browser headless")

    # Generate subcommand (enqueue messages)
    gen_parser = subparsers.add_parser("generate", help="Generate personalized messages")
    gen_parser.add_argument("--db", default="outreach.db", help="SQLite database path")
    gen_parser.add_argument("--max", type=int, default=25, help="Max messages to generate")
    gen_parser.add_argument("--openai", action="store_true", help="Use OpenAI to generate messages")

    # Outreach subcommand (simulate sending)
    reach_parser = subparsers.add_parser("outreach", help="Simulate outreach messaging")
    reach_parser.add_argument("--db", default="outreach.db", help="SQLite database path")
    reach_parser.add_argument("--dry-run", action="store_true", help="Do not mark messages as sent")
    reach_parser.add_argument("--daily-cap", type=int, default=25, help="Max messages to mark as sent per day")
    reach_parser.add_argument("--max", type=int, help="Max messages to send this run")
    reach_parser.add_argument("--fast", action="store_true", help="Skip sleep between sends")

    args = parser.parse_args()

    if args.command == "scrape":
        scrape_linkedin_search(
            query=args.query,
            max_profiles=args.max,
            output_db=args.db,
            output_csv=args.csv,
            headless=args.headless,
        )

    elif args.command == "generate":
        profiles = load_profiles(args.db, limit=args.max)
        enqueue_messages(args.db, profiles=profiles, max_per_run=args.max, use_openai=args.openai)

    elif args.command == "outreach":
        send_outreach(
            args.db,
            daily_cap=args.daily_cap,
            dry_run=args.dry_run,
            max_to_send=args.max,
            allow_sleep=not args.fast,
        )


if __name__ == "__main__":
    main()
