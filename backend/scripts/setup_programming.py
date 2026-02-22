"""
Set up full station programming via the live API:
  - 5 playlist templates
  - 3 schedules (one per station) with time blocks
  - 9 station-specific rules (3 per station)
  - Deactivate broken global rules

Usage:
    cd backend && uv run python scripts/setup_programming.py
"""
import sys
import httpx

API_BASE = "https://api.kbrlive.com/api/v1"
CREDS = {"email": "admin", "password": "613Radio"}

# Station IDs
STATIONS = {
    "kbr_main":       "95358e09-1260-48d5-85b5-f851000773c0",
    "kbr_jerusalem":  "d0740efe-3f25-4067-96f8-d8bc84c64dc3",
    "all_day_stories": "13c2697e-b9f8-42e8-b97a-4e1afa3b5a0e",
}

# ── Playlist template definitions ──
PLAYLISTS = [
    {
        "name": "Morning Mix",
        "description": "Upbeat morning rotation with lively music, jingles, and spots",
        "slots": [
            {"position": 0, "asset_type": "music", "category": "lively"},
            {"position": 1, "asset_type": "jingle", "category": None},
            {"position": 2, "asset_type": "music", "category": "lively"},
            {"position": 3, "asset_type": "music", "category": None},
            {"position": 4, "asset_type": "spot", "category": None},
            {"position": 5, "asset_type": "music", "category": "lively"},
        ],
    },
    {
        "name": "Afternoon Mix",
        "description": "Balanced afternoon rotation with music, shiurim, and spots",
        "slots": [
            {"position": 0, "asset_type": "music", "category": None},
            {"position": 1, "asset_type": "jingle", "category": None},
            {"position": 2, "asset_type": "music", "category": None},
            {"position": 3, "asset_type": "shiur", "category": None},
            {"position": 4, "asset_type": "spot", "category": None},
            {"position": 5, "asset_type": "music", "category": None},
        ],
    },
    {
        "name": "Evening Family",
        "description": "Family-friendly evening with stories, relaxing music, and spots",
        "slots": [
            {"position": 0, "asset_type": "stories", "category": None},
            {"position": 1, "asset_type": "jingle", "category": None},
            {"position": 2, "asset_type": "stories", "category": None},
            {"position": 3, "asset_type": "music", "category": "relax"},
            {"position": 4, "asset_type": "spot", "category": None},
            {"position": 5, "asset_type": "stories", "category": None},
        ],
    },
    {
        "name": "Night Chill",
        "description": "Relaxing overnight music rotation",
        "slots": [
            {"position": 0, "asset_type": "music", "category": "relax"},
            {"position": 1, "asset_type": "music", "category": "relax"},
            {"position": 2, "asset_type": "jingle", "category": None},
            {"position": 3, "asset_type": "music", "category": "relax"},
            {"position": 4, "asset_type": "spot", "category": None},
            {"position": 5, "asset_type": "music", "category": "relax"},
        ],
    },
    {
        "name": "All Day Stories",
        "description": "Continuous stories rotation for the stories channel",
        "slots": [
            {"position": 0, "asset_type": "stories", "category": None},
            {"position": 1, "asset_type": "stories", "category": None},
            {"position": 2, "asset_type": "jingle", "category": None},
            {"position": 3, "asset_type": "stories", "category": None},
            {"position": 4, "asset_type": "stories", "category": None},
            {"position": 5, "asset_type": "spot", "category": None},
            {"position": 6, "asset_type": "stories", "category": None},
        ],
    },
]

# ── Schedule block definitions ──
MAIN_BLOCKS = [
    {"name": "Morning Drive",  "start_time": "06:00:00", "end_time": "12:00:00", "playlist": "Morning Mix"},
    {"name": "Afternoon",      "start_time": "12:00:00", "end_time": "18:00:00", "playlist": "Afternoon Mix"},
    {"name": "Evening Family", "start_time": "18:00:00", "end_time": "22:00:00", "playlist": "Evening Family"},
    {"name": "Night Chill",    "start_time": "22:00:00", "end_time": "06:00:00", "playlist": "Night Chill"},
]

STORIES_BLOCKS = [
    {"name": "All Day", "start_time": "00:00:00", "end_time": "23:59:59", "playlist": "All Day Stories"},
]

# ── Rules to create per station ──
RULES_PER_STATION = [
    {
        "name": "Hourly Time Announcement",
        "description": "Play time announcement jingle every hour",
        "rule_type": "interval",
        "asset_type": "jingle",
        "category": "time_announcement",
        "interval_minutes": 60,
        "priority": 95,
    },
    {
        "name": "Hourly Weather Report",
        "description": "Play weather spot every hour",
        "rule_type": "interval",
        "asset_type": "spot",
        "category": "weather_spot",
        "interval_minutes": 60,
        "priority": 90,
    },
    {
        "name": "Sponsor Ad Every 30min",
        "description": "Insert sponsor ad spot every 30 minutes",
        "rule_type": "interval",
        "asset_type": "spot",
        "category": None,
        "interval_minutes": 30,
        "priority": 50,
    },
]

# ── Broken global rules to deactivate ──
BROKEN_RULES_TO_DEACTIVATE = [
    "Zmanim Announcements",
    "Friday Shabbos Music",
]


def login(client: httpx.Client) -> str:
    """Authenticate and return JWT token."""
    resp = client.post(f"{API_BASE}/auth/login", json=CREDS)
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print(f"Authenticated as admin")
    return token


def create_playlists(client: httpx.Client, headers: dict) -> dict[str, str]:
    """Create playlist templates. Returns name -> id mapping."""
    template_ids = {}

    # Check existing templates first
    resp = client.get(f"{API_BASE}/playlists", headers=headers)
    resp.raise_for_status()
    existing = {t["name"]: t["id"] for t in resp.json()}

    for pl in PLAYLISTS:
        if pl["name"] in existing:
            print(f"  Playlist '{pl['name']}' already exists, skipping")
            template_ids[pl["name"]] = existing[pl["name"]]
            continue

        resp = client.post(
            f"{API_BASE}/playlists",
            headers=headers,
            json={
                "name": pl["name"],
                "description": pl["description"],
                "slots": pl["slots"],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        template_ids[pl["name"]] = data["id"]
        print(f"  Created playlist '{pl['name']}' ({data['id']}) with {len(pl['slots'])} slots")

    return template_ids


def create_schedule_with_blocks(
    client: httpx.Client,
    headers: dict,
    station_id: str,
    schedule_name: str,
    blocks: list[dict],
    template_ids: dict[str, str],
) -> str:
    """Create a schedule and its blocks. Returns schedule_id."""
    # Check if schedule already exists
    resp = client.get(f"{API_BASE}/schedules/", headers=headers, params={"station_id": station_id})
    resp.raise_for_status()
    existing = resp.json()
    for s in existing:
        if s["name"] == schedule_name:
            print(f"  Schedule '{schedule_name}' already exists, skipping")
            return s["id"]

    # Create schedule
    resp = client.post(
        f"{API_BASE}/schedules/",
        headers=headers,
        json={
            "station_id": station_id,
            "name": schedule_name,
            "description": f"Auto-generated programming for {schedule_name}",
            "is_active": True,
            "priority": 10,
        },
    )
    resp.raise_for_status()
    schedule_id = resp.json()["id"]
    print(f"  Created schedule '{schedule_name}' ({schedule_id})")

    # Create blocks
    for block in blocks:
        playlist_name = block["playlist"]
        template_id = template_ids.get(playlist_name)

        resp = client.post(
            f"{API_BASE}/schedules/blocks",
            headers=headers,
            json={
                "schedule_id": schedule_id,
                "name": block["name"],
                "start_time": block["start_time"],
                "end_time": block["end_time"],
                "recurrence_type": "daily",
                "playback_mode": "shuffle",
                "priority": 0,
                "playlist_template_id": template_id,
            },
        )
        resp.raise_for_status()
        block_id = resp.json()["id"]
        print(f"    Block '{block['name']}' ({block['start_time']}-{block['end_time']}) -> {playlist_name}")

    return schedule_id


def create_station_rules(client: httpx.Client, headers: dict, station_id: str, station_name: str):
    """Create rules for a specific station."""
    # Check existing rules for this station
    resp = client.get(f"{API_BASE}/rules", headers=headers, params={"station_id": station_id})
    resp.raise_for_status()
    existing_rules = resp.json().get("rules", [])
    existing_names = {r["name"] for r in existing_rules if r.get("station_id") == station_id}

    for rule_def in RULES_PER_STATION:
        rule_name = f"{rule_def['name']} ({station_name})"
        if rule_name in existing_names:
            print(f"    Rule '{rule_name}' already exists, skipping")
            continue

        payload = {
            "name": rule_name,
            "description": rule_def["description"],
            "rule_type": rule_def["rule_type"],
            "asset_type": rule_def["asset_type"],
            "category": rule_def["category"],
            "interval_minutes": rule_def["interval_minutes"],
            "priority": rule_def["priority"],
            "is_active": True,
            "station_id": station_id,
        }
        resp = client.post(f"{API_BASE}/rules", headers=headers, json=payload)
        resp.raise_for_status()
        print(f"    Created rule '{rule_name}' (priority={rule_def['priority']})")


def deactivate_broken_rules(client: httpx.Client, headers: dict):
    """Deactivate global rules that reference empty categories."""
    resp = client.get(f"{API_BASE}/rules", headers=headers, params={"limit": 100})
    resp.raise_for_status()
    rules = resp.json().get("rules", [])

    for rule in rules:
        if rule["name"] in BROKEN_RULES_TO_DEACTIVATE and rule["is_active"]:
            resp = client.put(
                f"{API_BASE}/rules/{rule['id']}",
                headers=headers,
                json={"is_active": False},
            )
            resp.raise_for_status()
            print(f"  Deactivated broken rule: '{rule['name']}'")


def main():
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        # 1. Authenticate
        token = login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Create playlist templates
        print("\n=== Creating Playlist Templates ===")
        template_ids = create_playlists(client, headers)
        print(f"  {len(template_ids)} templates ready")

        # 3. Create schedules with blocks
        print("\n=== Creating Schedules & Blocks ===")

        print("\n  KBR Main:")
        create_schedule_with_blocks(
            client, headers,
            STATIONS["kbr_main"], "KBR Main Daily",
            MAIN_BLOCKS, template_ids,
        )

        print("\n  KBR Jerusalem:")
        create_schedule_with_blocks(
            client, headers,
            STATIONS["kbr_jerusalem"], "Jerusalem Daily",
            MAIN_BLOCKS, template_ids,
        )

        print("\n  All Day Stories:")
        create_schedule_with_blocks(
            client, headers,
            STATIONS["all_day_stories"], "Stories Programming",
            STORIES_BLOCKS, template_ids,
        )

        # 4. Create station-specific rules
        print("\n=== Creating Station-Specific Rules ===")

        print("\n  KBR Main:")
        create_station_rules(client, headers, STATIONS["kbr_main"], "KBR Main")

        print("\n  KBR Jerusalem:")
        create_station_rules(client, headers, STATIONS["kbr_jerusalem"], "Jerusalem")

        print("\n  All Day Stories:")
        create_station_rules(client, headers, STATIONS["all_day_stories"], "Stories")

        # 5. Deactivate broken global rules
        print("\n=== Deactivating Broken Global Rules ===")
        deactivate_broken_rules(client, headers)

        print("\n=== Done! ===")
        print("Verify at:")
        print("  https://kbrlive.com/admin/schedules")
        print("  https://kbrlive.com/admin/playlists")
        print("  https://kbrlive.com/admin/rules")


if __name__ == "__main__":
    main()
