"""Outreach workflow manager.

This module provides utilities for scheduling, logging, and simulating outreach.
It is built to be "semi-automated": it prepares messages, enforces rate limits and caps,
and logs what would be sent, but does not automatically bypass platform protections.

The core workflow is:
1) Load candidate profiles from the database
2) Generate a personalized message for each
3) Enqueue each message into an outbound log table
4) (Optional) Simulate sending messages with delays

"""

from __future__ import annotations

import logging
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence

from message_generator import generate_message

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


@dataclass
class OutreachMessage:
    outreach_id: int
    profile_id: int
    profile_name: str
    profile_headline: str
    profile_url: str
    message_text: str
    created_at: float
    sent_at: Optional[float] = None


def init_outreach_db(path: str) -> None:
    """Initialize the outreach database schema."""

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            headline TEXT,
            profile_url TEXT UNIQUE,
            extracted_at REAL NOT NULL
        )"""
    )

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id INTEGER NOT NULL,
            message_text TEXT NOT NULL,
            created_at REAL NOT NULL,
            sent_at REAL,
            UNIQUE(profile_id)
        )"""
    )

    conn.commit()
    conn.close()


def load_profiles(db_path: str, limit: Optional[int] = None) -> Sequence[dict]:
    """Load profiles from the database, optionally limiting how many to return."""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = "SELECT id, name, headline, profile_url FROM profiles ORDER BY extracted_at DESC"
    if limit:
        query += " LIMIT ?"
        cursor.execute(query, (limit,))
    else:
        cursor.execute(query)

    rows = cursor.fetchall()
    conn.close()

    return [
        {"id": row[0], "name": row[1], "headline": row[2] or "", "profile_url": row[3]} for row in rows
    ]


def _profile_message_exists(conn: sqlite3.Connection, profile_id: int) -> bool:
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM outreach WHERE profile_id = ?", (profile_id,))
    return cursor.fetchone() is not None


def enqueue_messages(
    db_path: str,
    profiles: Sequence[dict],
    max_per_run: Optional[int] = None,
    use_openai: bool = False,
) -> int:
    """Generate and store outreach messages for profiles.

    This does NOT send messages. It simply creates a queue of messages to be sent.
    """

    init_outreach_db(db_path)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    created = 0
    for profile in profiles:
        if max_per_run and created >= max_per_run:
            break

        profile_id = profile["id"]
        if _profile_message_exists(conn, profile_id):
            continue

        text = generate_message(profile["name"], profile["headline"], use_openai=use_openai)
        cursor.execute(
            "INSERT OR IGNORE INTO outreach (profile_id, message_text, created_at) VALUES (?, ?, ?)",
            (profile_id, text, time.time()),
        )
        created += 1

    conn.commit()
    conn.close()
    logger.info("Enqueued %d new messages", created)
    return created


def list_pending_messages(db_path: str, limit: Optional[int] = None) -> Sequence[OutreachMessage]:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = "SELECT o.id, p.id, p.name, p.headline, p.profile_url, o.message_text, o.created_at, o.sent_at "
    query += "FROM outreach o JOIN profiles p ON o.profile_id = p.id "
    query += "WHERE o.sent_at IS NULL ORDER BY o.created_at ASC"
    if limit:
        query += " LIMIT ?"
        cursor.execute(query, (limit,))
    else:
        cursor.execute(query)

    rows = cursor.fetchall()
    conn.close()

    return [
        OutreachMessage(
            outreach_id=row[0],
            profile_id=row[1],
            profile_name=row[2],
            profile_headline=row[3],
            profile_url=row[4],
            message_text=row[5],
            created_at=row[6],
            sent_at=row[7],
        )
        for row in rows
    ]


def _mark_sent(conn: sqlite3.Connection, outreach_id: int) -> None:
    cursor = conn.cursor()
    cursor.execute("UPDATE outreach SET sent_at = ? WHERE id = ?", (time.time(), outreach_id))


def _daily_sent_count(conn: sqlite3.Connection, date: datetime) -> int:
    start = datetime(date.year, date.month, date.day)
    end = start + timedelta(days=1)

    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(1) FROM outreach WHERE sent_at BETWEEN ? AND ?", (start.timestamp(), end.timestamp())
    )
    return cursor.fetchone()[0]


def send_outreach(
    db_path: str,
    daily_cap: int = 25,
    min_delay_s: int = 30,
    max_delay_s: int = 120,
    dry_run: bool = True,
    max_to_send: Optional[int] = None,
    allow_sleep: bool = True,
) -> int:
    """Simulate sending outreach messages.

    This function intentionally does not perform any automated platform actions.
    Instead, it logs which messages would be sent and updates the database to mark them as "sent".

    It enforces rate limiting and a daily cap.
    """

    init_outreach_db(db_path)
    conn = sqlite3.connect(db_path)

    sent_today = _daily_sent_count(conn, datetime.utcnow())
    if sent_today >= daily_cap:
        logger.info("Daily cap reached (%d/%d). No messages will be sent.", sent_today, daily_cap)
        conn.close()
        return 0

    allowed = daily_cap - sent_today
    if max_to_send is not None:
        allowed = min(allowed, max_to_send)

    pending = list_pending_messages(db_path, limit=allowed)
    logger.info("Preparing to send %d messages (daily cap %d, sent today %d)", len(pending), daily_cap, sent_today)

    sent_count = 0
    for item in pending:
        # Simulate the action of sending (manual or via platform if integrated)
        logger.info("----\nTo: %s\nProfile: %s\nMessage: %s\n", item.profile_name, item.profile_url, item.message_text)
        if not dry_run:
            # In a real system, here is where you'd integrate with a browser automation step or API.
            _mark_sent(conn, outreach_id=item.outreach_id)
            conn.commit()
            sent_count += 1

            # Rate limiting: random delay between sends.
            delay = random.uniform(min_delay_s, max_delay_s)
            logger.info("Sleeping %.1f seconds before next message", delay)
            if allow_sleep:
                time.sleep(delay)
        else:
            # In dry-run mode, just log and do not mark as sent.
            sent_count += 0

    conn.close()
    return sent_count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Outreach message queue manager")
    parser.add_argument("--db", default="outreach.db", help="Path to outreach SQLite database")
    parser.add_argument("--enqueue", action="store_true", help="Generate outbound messages (does not send)")
    parser.add_argument("--send", action="store_true", help="Simulate sending outbound messages")
    parser.add_argument("--dry-run", action="store_true", help="Do not mark messages as sent")
    parser.add_argument("--max", type=int, help="Max messages to enqueue or send")
    parser.add_argument("--openai", action="store_true", help="Use OpenAI to generate messages")
    parser.add_argument("--daily-cap", type=int, default=25, help="Max messages per day")
    parser.add_argument("--fast", action="store_true", help="Do not sleep between simulated sends")
    args = parser.parse_args()

    if args.enqueue:
        profiles = load_profiles(args.db, limit=args.max)
        enqueue_messages(args.db, profiles, max_per_run=args.max, use_openai=args.openai)

    if args.send:
        send_outreach(
            args.db,
            daily_cap=args.daily_cap,
            dry_run=args.dry_run,
            max_to_send=args.max,
            allow_sleep=not args.fast,
        )
