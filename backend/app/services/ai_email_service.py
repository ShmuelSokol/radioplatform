"""
AI-powered email drafting using Claude API.
Generates professional email text for manager-to-sponsor outreach.
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def draft_email(context: dict) -> str:
    """
    Generate an AI-drafted email using Claude API.

    Args:
        context: Dict with keys like:
            - sponsor_name: str
            - campaign_name: str (optional)
            - purpose: str (e.g., "renewal", "welcome", "follow_up", "custom")
            - custom_instructions: str (optional)
            - recent_stats: dict (optional, e.g. total_plays, plays_this_month)

    Returns:
        The generated email body text.
    """
    if not settings.anthropic_enabled:
        return _fallback_draft(context)

    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    sponsor_name = context.get("sponsor_name", "Valued Sponsor")
    purpose = context.get("purpose", "general")
    campaign_name = context.get("campaign_name", "")
    custom_instructions = context.get("custom_instructions", "")
    recent_stats = context.get("recent_stats", {})

    stats_text = ""
    if recent_stats:
        stats_text = f"\nRecent performance data: {recent_stats}"

    prompt = f"""Write a professional, warm email from a radio station manager to a sponsor.

Sponsor name: {sponsor_name}
Purpose: {purpose}
{f'Campaign: {campaign_name}' if campaign_name else ''}
{f'Additional instructions: {custom_instructions}' if custom_instructions else ''}
{stats_text}

The email should be:
- Professional but friendly
- Concise (2-3 short paragraphs max)
- From "Kol Bramah Radio" station
- Include specific data if provided
- End with a clear call to action

Write only the email body (no subject line). Do not include "Dear" or signature — just the body paragraphs."""

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"AI email drafting failed: {e}")
        return _fallback_draft(context)


def _fallback_draft(context: dict) -> str:
    """Simple template fallback when Claude API is unavailable."""
    name = context.get("sponsor_name", "Valued Sponsor")
    purpose = context.get("purpose", "general")

    templates = {
        "welcome": f"Welcome to Kol Bramah Radio! We're excited to have {name} as a sponsor. Your ad will reach our dedicated listeners across multiple stations. We look forward to a successful partnership.",
        "renewal": f"We hope you've been pleased with the results of your sponsorship with Kol Bramah Radio. We'd love to continue our partnership. Please let us know if you'd like to renew your campaign.",
        "follow_up": f"Just following up on your recent campaign with Kol Bramah Radio. We'd love to hear your feedback and discuss how we can continue to help {name} reach your audience.",
        "general": f"Thank you for your continued partnership with Kol Bramah Radio. We value {name} as a sponsor and are always looking for ways to deliver the best results for your campaigns.",
    }

    return templates.get(purpose, templates["general"])
