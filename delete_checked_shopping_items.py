#!/usr/bin/env python3
"""Delete all checked items from every Mealie shopping list (stdlib only)."""
import json
import os
import sys
import urllib.parse
import urllib.request

def _load_conf():
    """Read KEY="value" lines from the shared mealie.conf (same file the .sh scripts source)."""
    path = os.environ.get("MEALIE_CONF", "/mnt/user/appdata/mealie-scripts/mealie.conf")
    conf = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                val = val.strip()
                if val[:1] in "\"'" and val[:1] and val.find(val[0], 1) > 0:
                    val = val[1:val.find(val[0], 1)]
                else:
                    val = val.split(" #")[0].strip()
                conf[key.strip()] = val
    except OSError as e:
        sys.exit(f"Cannot read {path}: {e}\nCopy mealie.conf.example there and fill it in.")
    return conf


CONF = _load_conf()
DATA_DIR = CONF.get("DATA_DIR", "/mnt/user/appdata/mealie-scripts")

MEALIE_URL = CONF.get("MEALIE_URL", "").rstrip("/")
MEALIE_API = CONF.get("MEALIE_API_KEY", "")
DRY_RUN = CONF.get("APPLY", "0") != "1"   # APPLY=1 in mealie.conf to really delete
if not MEALIE_URL or not MEALIE_API:
    sys.exit("Set MEALIE_URL and MEALIE_API_KEY in mealie.conf first.")

BATCH = 50


def api(method, path, params=None):
    url = f"{MEALIE_URL}/api{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {MEALIE_API}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def main():
    print(f"DRY_RUN = {DRY_RUN}\n")
    lists = api("GET", "/households/shopping/lists", {"perPage": -1})["items"]
    total = 0

    for sl in lists:
        full = api("GET", f"/households/shopping/lists/{sl['id']}")
        checked = [i for i in full.get("listItems", []) if i.get("checked")]
        if not checked:
            print(f"[{sl['name']}] no checked items")
            continue

        print(f"[{sl['name']}] {len(checked)} checked item(s)")
        for i in checked:
            print(f"   - {i.get('display') or i.get('note') or i['id']}")
        total += len(checked)

        if DRY_RUN:
            continue

        ids = [i["id"] for i in checked]
        for n in range(0, len(ids), BATCH):
            api("DELETE", "/households/shopping/items", {"ids": ids[n:n + BATCH]})

    print(f"\n{'Would delete' if DRY_RUN else 'Deleted'}: {total} item(s)")


if __name__ == "__main__":
    main()
