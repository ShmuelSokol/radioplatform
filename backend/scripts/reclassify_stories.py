"""
Reclassify Carlebach assets as 'stories' based on duration or title keywords.

Stories/talks are typically >10 minutes, while songs are 3-7 min.

Uses the live API (no direct DB connection needed).

Usage:
    cd backend && uv run python scripts/reclassify_stories.py
"""
import httpx

API_BASE = "https://api.kbrlive.com/api/v1"
CREDS = {"email": "admin", "password": "613Radio"}

# Keywords that indicate a story/talk (case-insensitive)
STORY_KEYWORDS = [
    "story", "sippur", "torah", "talk", "lecture",
    "drasha", "vort", "mayse", "tale", "monologue",
    "stories", "parsha", "shiur",
]

# Duration threshold: >10 minutes = likely a story/talk
DURATION_THRESHOLD = 600.0  # seconds


def _is_story_by_title(title: str) -> bool:
    """Check if asset title contains story/talk keywords."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in STORY_KEYWORDS)


def main():
    with httpx.Client(timeout=30) as client:
        # Authenticate
        resp = client.post(f"{API_BASE}/auth/login", json=CREDS)
        resp.raise_for_status()
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("Authenticated as admin")

        # Fetch all Carlebach assets (paginated)
        all_carlebach = []
        skip = 0
        limit = 200
        while True:
            resp = client.get(
                f"{API_BASE}/assets",
                headers=headers,
                params={"artist_search": "carlebach", "skip": skip, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
            assets = data.get("assets", [])
            all_carlebach.extend(assets)
            if len(assets) < limit:
                break
            skip += limit

        total_carlebach = len(all_carlebach)
        print(f"Total Carlebach assets: {total_carlebach}")

        # Find candidates (not already stories)
        candidates = [a for a in all_carlebach if a.get("asset_type") != "stories"]
        print(f"Non-story Carlebach assets to check: {len(candidates)}")

        reclassified_by_duration = 0
        reclassified_by_title = 0
        reclassified = []

        for asset in candidates:
            reason = None
            duration = asset.get("duration") or 0
            title = asset.get("title", "")

            if duration > DURATION_THRESHOLD:
                reason = "duration"
                reclassified_by_duration += 1
            elif _is_story_by_title(title):
                reason = "title"
                reclassified_by_title += 1

            if reason:
                reclassified.append(asset)
                dur_str = f"{duration:.0f}s" if duration else "no duration"
                print(f"  [{reason}] {title} ({dur_str})")

        # Update each asset via API
        for asset in reclassified:
            resp = client.patch(
                f"{API_BASE}/assets/{asset['id']}",
                headers=headers,
                json={"asset_type": "stories"},
            )
            if resp.status_code == 200:
                pass  # success
            else:
                print(f"  WARN: Failed to update {asset['id']}: {resp.status_code}")

        # Count total stories now
        resp = client.get(
            f"{API_BASE}/assets",
            headers=headers,
            params={"asset_type": "stories", "limit": 1},
        )
        resp.raise_for_status()
        total_stories = resp.json().get("total", 0)

        print(f"\n=== Summary ===")
        print(f"Reclassified by duration (>{DURATION_THRESHOLD/60:.0f} min): {reclassified_by_duration}")
        print(f"Reclassified by title keywords: {reclassified_by_title}")
        print(f"Total reclassified: {len(reclassified)}")
        print(f"Total stories in DB now: {total_stories}")


if __name__ == "__main__":
    main()
