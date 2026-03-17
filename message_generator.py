"""Personalization engine for outreach messages.

This module generates a human-feeling first message that references the recipient's
background and interests without sounding like a recruiter blast.

It can optionally augment messages using OpenAI if an API key is provided.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Optional

try:
    import openai
except ImportError:  # pragma: no cover
    openai = None  # type: ignore


@dataclass
class MessageContext:
    name: str
    headline: str


def _normalize_headline(headline: str) -> str:
    """Clean up the headline to make it suitable for message templates."""

    return headline.strip().rstrip("." )


def generate_message(name: str, headline: str, use_openai: bool = False) -> str:
    """Generate a personalized message.

    If `use_openai=True` and an API key is configured, this will use a GPT-style model
    to create more varied and human-sounding messages.
    """

    name = name.strip().split(" ")[0] if name.strip() else "there"
    clean_headline = _normalize_headline(headline)

    if use_openai and openai is not None and os.getenv("OPENAI_API_KEY"):
        return _generate_message_openai(name, clean_headline)

    return _generate_message_template(name, clean_headline)


def _generate_message_template(name: str, headline: str) -> str:
    """Generate a message using fixed templates.

    This ensures we never accidentally include links in the first outreach message.
    """

    templates = [
        """Hey {name}! I came across your profile while looking for UW students who are into {headline}. Electrium Mobility is a student team building electric boards/scooters, and we’re always looking for people who want hands-on experience. If you’re curious, I’d love to share more about what we’re building (no commitments!).""",
        """Hi {name}, I noticed you’re studying {headline} at Waterloo. If you enjoy building real hardware + software projects, you might like what we do at Electrium Mobility. Happy to answer any questions about the team — just say the word.""",
        """Hello {name}! As a fellow Waterloo enthusiast, I love seeing people working on {headline}. The Electrium team is working on sustainable e-mobility projects and is beginner-friendly. Let me know if you want to hear about how to get involved (no pressure!).""",
        """Hey {name}, saw your experience with {headline} and thought you might appreciate how we make real-world projects happen in Electrium Mobility. We focus on learning by doing, and I’d be glad to share what steps we take for new members — just ask.""",
    ]

    template = random.choice(templates)
    return template.format(name=name, headline=headline)


def _generate_message_openai(name: str, headline: str) -> str:
    """Generate a more tailored message using OpenAI API.

    This is optional; without an API key the fallback template generator will be used.
    """

    if openai is None:
        return _generate_message_template(name, headline)

    openai.api_key = os.getenv("OPENAI_API_KEY")

    prompt = """You are helping a member of a student design team at the University of Waterloo reach out to students in Computer Science and Engineering.
Generate a brief, friendly, human-sounding first message to send on LinkedIn. Mention the recipient's area of interest or study, but do not include links or anything that looks like a sales pitch. Keep it under 130 words.

Recipient name: {name}
Recipient headline: {headline}

Message:
""".format(name=name, headline=headline)

    response = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=220,
        temperature=0.8,
    )

    text = response.choices[0].message.content.strip()
    # Make sure we never include a link in the first message.
    return text.replace("http://", "").replace("https://", "")


if __name__ == "__main__":
    # Quick sanity check when executed directly.
    print(generate_message("Alicia", "Computer Science Student at University of Waterloo", use_openai=False))
