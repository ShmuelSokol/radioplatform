"""Read TYPESAFE_API_KEY from backend/.env and set it on the Railway service.
Never prints the key. Run: python scripts/push_typesafe_key.py
"""
import json, os, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(ROOT, "backend", ".env")
PROJECT = "63f6510e-b20e-42ae-ae67-ff1ba79b4bd7"
ENVIRONMENT = "241aa72f-001c-4efa-be5a-eb83bd026117"
SERVICE = "21ce96a5-17f9-4a29-afb8-6e5e827623d0"

vals = {}
for line in open(ENV, encoding="utf-8"):
    if "=" in line and not line.startswith("#"):
        k, v = line.strip().split("=", 1)
        vals[k] = v.strip().strip('"').strip("'")
key = vals.get("TYPESAFE_API_KEY")
TOKEN = vals.get("RAILWAY_TOKEN") or sys.exit("RAILWAY_TOKEN not set in backend/.env")
if not key or key == "PASTE_KEY_HERE":
    sys.exit("TYPESAFE_API_KEY not set in backend/.env yet")

q = {
    "query": "mutation($i: VariableUpsertInput!){ variableUpsert(input: $i) }",
    "variables": {"i": {"projectId": PROJECT, "environmentId": ENVIRONMENT,
                        "serviceId": SERVICE, "name": "TYPESAFE_API_KEY", "value": key}},
}
req = urllib.request.Request("https://backboard.railway.app/graphql/v2",
    data=json.dumps(q).encode(), headers={"Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json", "User-Agent": "curl/8"})
body = json.loads(urllib.request.urlopen(req).read())
if body.get("errors"):
    sys.exit("Railway error: " + json.dumps(body["errors"]))
print(f"TYPESAFE_API_KEY set on Railway (length {len(key)}, starts with {key[:3]}...)")
