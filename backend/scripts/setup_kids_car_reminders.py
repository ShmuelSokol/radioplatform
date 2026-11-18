"""
Kids-in-car safety reminders (Shmuel, 2026-11-18): upload the siren and the recordings,
then give each station two seasonal program blocks:

  "Kids in car reminder - summer"        siren -> a summer version, only in its summer
  "Kids in car reminder - rest of year"  siren -> a rest-of-year version, the rest of the year

Each block plays the siren, then the version that has rested longest (category rotation).
Soft (after the song), at minutes :20 / :50 so they never share a minute with the :00/:30
hard blocks or the :15/:45 sponsorship places. Safe to run again: it reuses uploads with the
same title and updates blocks with the same name instead of adding new ones.

A station whose blocks would REPLACE its built-in hourly time and weather (no active block
yet and blocks_keep_hourly off) gets blocks_keep_hourly = true first, so adding these
reminders never takes its time checks away (wiki/program-blocks.md).

Usage (PowerShell on the PC, recordings in one folder, file names starting with
"summer" or "year", e.g. summer-1.mp3, year-2.mp3; the siren as siren.mp3):

    cd C:\\Users\\shmue\\KBR\\radioplatform\\backend
    $env:KBR_ADMIN_PASSWORD = "..."
    uv run python scripts/setup_kids_car_reminders.py C:\\path\\to\\kids-car [--dry-run]
"""

import os
import sys
from pathlib import Path

import httpx

API_BASE = os.environ.get("KBR_API", "https://api.kbrlive.com/api/v1")
CREDS = {
    "email": os.environ.get("KBR_ADMIN_EMAIL", "admin"),
    "password": os.environ["KBR_ADMIN_PASSWORD"],  # never hard-code: this repo is public
}

STATIONS = {
    "Kol B'ramah Main": "95358e09-1260-48d5-85b5-f851000773c0",
    "KBR Jerusalem": "d0740efe-3f25-4067-96f8-d8bc84c64dc3",
    "Kol B'ramah - All Day Stories": "13c2697e-b9f8-42e8-b97a-4e1afa3b5a0e",
}

# Summer = when a parked car turns deadly fastest. Lakewood: mid-May to mid-September.
# Jerusalem's hot season runs longer: May 1 to October 15. Edit on the Program Blocks page.
SEASONS = {
    "KBR Jerusalem": (("05-01", "10-15"), ("10-16", "04-30")),
}
DEFAULT_SEASON = (("05-15", "09-15"), ("09-16", "05-14"))

SUMMER_TIMES = "08:20,11:50,14:20,17:50"  # four a day in the heat
YEAR_TIMES = "08:20,17:50"  # school run and the evening drive home

SIREN_TITLE = "Siren intro (kids in car reminder)"
SUMMER_CATEGORY, YEAR_CATEGORY = "kids_car_summer", "kids_car_year"


def login(client: httpx.Client) -> dict:
    res = client.post(f"{API_BASE}/auth/login", json=CREDS)
    res.raise_for_status()
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def find_asset(client, headers, title: str) -> dict | None:
    res = client.get(f"{API_BASE}/assets", params={"search": title, "limit": 50}, headers=headers)
    res.raise_for_status()
    body = res.json()
    rows = body.get("assets", body) if isinstance(body, dict) else body
    return next((a for a in rows if a.get("title") == title), None)


def upload(client, headers, path: Path, title: str, asset_type: str, category: str, dry: bool) -> str:
    found = find_asset(client, headers, title)
    if found:
        print(f"  have   {title}")
        return found["id"]
    print(f"  upload {title}  ({asset_type} / {category})")
    if dry:
        return f"dry-{title}"
    with path.open("rb") as f:
        res = client.post(
            f"{API_BASE}/assets/upload",
            files={"file": (path.name, f, "audio/mpeg")},
            data={"title": title, "asset_type": asset_type, "category": category, "format": "mp3"},
            headers=headers,
            timeout=300,
        )
    res.raise_for_status()
    return res.json()["id"]


def recording_title(path: Path, kind: str) -> str:
    n = "".join(ch for ch in path.stem if ch.isdigit()) or "1"
    return f"Kids in car reminder - {'summer' if kind == 'summer' else 'rest of year'} {n}"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__)
    folder = Path(args[0])
    siren = folder / "siren.mp3"
    summer = sorted(p for p in folder.glob("summer*.mp3"))
    year = sorted(p for p in folder.glob("year*.mp3"))
    if not siren.exists() or not summer or not year:
        sys.exit(f"Need siren.mp3, summer*.mp3 and year*.mp3 in {folder}")

    with httpx.Client(timeout=60, follow_redirects=True) as client:
        headers = login(client)
        print("Library:")
        siren_id = upload(client, headers, siren, SIREN_TITLE, "jingle", "block_only", dry)
        for p in summer:
            upload(client, headers, p, recording_title(p, "summer"), "spot", SUMMER_CATEGORY, dry)
        for p in year:
            upload(client, headers, p, recording_title(p, "year"), "spot", YEAR_CATEGORY, dry)

        caps = client.get(f"{API_BASE}/program-blocks/capabilities", headers=headers)
        if caps.status_code != 200 or not caps.json().get("seasons"):
            sys.exit("The server does not know block seasons yet (deploy the PR first).")

        for name, station_id in STATIONS.items():
            print(f"{name}:")
            station = client.get(f"{API_BASE}/stations/{station_id}", headers=headers).json()
            blocks = client.get(
                f"{API_BASE}/program-blocks", params={"station_id": station_id}, headers=headers
            ).json()
            mine = {b["name"]: b for b in blocks if b["name"].startswith("Kids in car reminder")}
            others = [b for b in blocks if b["name"] not in mine]
            config = dict(station.get("automation_config") or {})
            if not any(b["is_active"] for b in others) and not config.get("blocks_keep_hourly"):
                print("  no blocks yet: keeping its hourly time and weather (blocks_keep_hourly)")
                if not dry:
                    config["blocks_keep_hourly"] = True
                    client.put(
                        f"{API_BASE}/stations/{station_id}",
                        json={"automation_config": config},
                        headers=headers,
                    ).raise_for_status()
            taken = {
                t for b in others if b["is_active"] for t in b["times"].split(",")
            }
            clash = taken & set(SUMMER_TIMES.split(",") + YEAR_TIMES.split(","))
            if clash:
                sys.exit(f"  {name} already has a block at {sorted(clash)}: pick other minutes")
            (s_start, s_end), (y_start, y_end) = SEASONS.get(name, DEFAULT_SEASON)
            for block_name, times, start, end, category in (
                ("Kids in car reminder - summer", SUMMER_TIMES, s_start, s_end, SUMMER_CATEGORY),
                ("Kids in car reminder - rest of year", YEAR_TIMES, y_start, y_end, YEAR_CATEGORY),
            ):
                body = {
                    "name": block_name,
                    "days_of_week": "0,1,2,3,4,5,6",
                    "times": times,
                    "timing_mode": "soft",
                    "latest_minutes": None,
                    "season_start": start,
                    "season_end": end,
                    "items": [
                        {"kind": "asset", "asset_id": siren_id, "label": "Siren"},
                        {
                            "kind": "category",
                            "asset_type": "spot",
                            "category": category,
                            "label": "Kids in car reminder",
                        },
                    ],
                }
                print(f"  {block_name}: {times}, {start} to {end} each year")
                if dry:
                    continue
                if block_name in mine:
                    res = client.put(
                        f"{API_BASE}/program-blocks/{mine[block_name]['id']}", json=body, headers=headers
                    )
                else:
                    res = client.post(
                        f"{API_BASE}/program-blocks",
                        json={**body, "station_id": station_id, "is_active": True},
                        headers=headers,
                    )
                res.raise_for_status()
    print("Done." if not dry else "Dry run: nothing changed.")


if __name__ == "__main__":
    main()
