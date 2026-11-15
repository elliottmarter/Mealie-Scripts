#!/usr/bin/env python3
"""
Keep Mealie's foods and shopping lists tidy, with Claude making the judgement calls.
Runs as-is from Unraid User Scripts (python3, stdlib only). Schedule it every 5 minutes (custom cron: */5 * * * *).

Each run does three things:
  1. LINK  - loose shopping list items (typed in with no food attached) are linked to a food. Claude either picks
             an existing food (the item text is then added to that food as an alias, e.g. "yellow onions" becomes
             an alias of "brown onion") or proposes a new food, which is created. Only runs once the list has been
             quiet for QUIET_MINUTES, so a burst of adds is handled in one go.
  2. LABEL - every food with no aisle label gets one of the 15 labels below.
  3. SYNC  - shopping list items take the aisle label of their food so the list sorts by shop layout.
Existing labels are matched by exact name (the numbers keep them in shop order, e.g. "01 Fruit & Veg").
Any of the 15 that are missing are created.

1. Fill in MEALIE_URL, MEALIE_API_KEY and ANTHROPIC_API_KEY in mealie.conf (see mealie.conf.example).
2. Run with APPLY=0 in mealie.conf: decisions are printed and saved to the cache files, nothing changes in Mealie.
   (Dry runs still ignore the quiet period if FORCE = True.)
3. Review the output. To override a decision, edit a cache file:
     CACHE_FILE       food name (lower case) -> label
     ITEM_CACHE_FILE  item text (lower case) -> {"action": "existing"|"new"|"skip", "food": "name"}
4. Set APPLY=1 in mealie.conf, LIMIT = 1 below, run, check the result in Mealie. Then LIMIT = 0 and schedule it.
   Cached decisions are reused, so only text Claude hasn't seen before costs an API call.
"""
import json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timezone

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

MEALIE_URL = CONF.get("MEALIE_URL", "")
MEALIE_TOKEN = CONF.get("MEALIE_API_KEY", "")
ANTHROPIC_API_KEY = CONF.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-5-5"                         # cheap and plenty for this job
APPLY = CONF.get("APPLY", "0") == "1"             # set APPLY=1 in mealie.conf to make real changes
LIMIT = 0                                          # 0 = all; e.g. 1 for a single-item/food test (per phase)
ONLY_UNLABELLED = True                             # False = also re-check foods that already have a label
BATCH_SIZE = 100                                   # foods/items per Claude request
LINK_ITEMS = True                                  # phase 1 on/off
QUIET_MINUTES = 15                                 # wait this long after the last list change before linking
FORCE = False                                      # True = ignore the quiet period (handy for testing)
CACHE_FILE = os.path.join(DATA_DIR, "ai_labels.json")
ITEM_CACHE_FILE = os.path.join(DATA_DIR, "ai_items.json")
LOCK_FILE = os.path.join(DATA_DIR, "run.lock")

# In shop walking order (reorder in Mealie's shopping list view).
LABEL_COLORS = {
    "01 Fruit & Veg": "#4caf50",
    "02 Herbs & Spices": "#8bc34a",
    "03 Pasta & Rice": "#ffc107",
    "04 Meat & Fish": "#b71c1c",
    "05 Chilled & Deli": "#3f51b5",
    "06 Dairy": "#fdd835",
    "07 Baking": "#ff9800",
    "08 Tins & Jars": "#9e9e9e",
    "09 Sauces & Condiments": "#e53935",
    "10 Snacks & Sweets": "#e91e63",
    "11 Household": "#8d6e63",
    "12 Toiletries": "#ba68c8",
    "13 Bakery": "#c68642",
    "14 Frozen": "#80deea",
    "15 Drinks": "#03a9f4",
}
LABELS = list(LABEL_COLORS)

SYSTEM = """You sort grocery items into the aisle of a UK discount supermarket (Lidl/Aldi) so a shopping list follows the store layout.
Pick exactly one label per item from the allowed list. Names may be US-style (cilantro = coriander, zucchini = courgette, ground beef = mince, eggplant = aubergine) or include packaging words like "pkg"; judge by the food itself.
Guidance:
- 01 Fruit & Veg: fresh produce and fresh herbs, incl. bagged salad.
- 13 Bakery: bread, wraps, tortillas, pitta, muffins.
- 08 Tins & Jars: tinned veg/beans/fish/tomatoes, olives, nut butters, honey, jarred pickles.
- 03 Pasta & Rice: dry pasta, rice, noodles, grains, couscous, lentils, oats, breakfast cereal.
- 09 Sauces & Condiments: ketchup, mayo, dressings, vinegars, soy/fish sauce, pastes, pesto, jarred cooking sauces.
- 07 Baking: flour, sugar, baking powder/soda, cornflour, cocoa, baking chocolate, vanilla, syrups, oils, stock, nuts, seeds, dried fruit.
- 02 Herbs & Spices: dried herbs, ground and whole spices, spice blends and seasonings, salt, pepper. Fresh herbs stay in 01 Fruit & Veg.
- 10 Snacks & Sweets: crisps, chocolate bars, biscuits, sweets.
- 15 Drinks: juice, soft drinks, water, alcohol, tea, coffee.
- 04 Meat & Fish: fresh raw meat and poultry, fresh or smoked fish and seafood.
- 06 Dairy: milk (incl. plant milk), butter, cream, yoghurt, eggs, cheese.
- 05 Chilled & Deli: cooked/cured meats, hummus, dips, fresh pasta, gnocchi, tofu/tempeh, chilled meat-free.
- 14 Frozen: anything frozen.
- 11 Household: cleaning products, kitchen roll, foil, bin bags, other non-food household items.
- 12 Toiletries: personal care (toothpaste, shampoo, soap, deodorant), medicines and first aid.
Return every item via the assign_labels tool, using the index given for each item."""

ITEM_SYSTEM = """You tidy a UK household's shopping list. Each list entry was typed in free-hand. For each one, decide how it maps to the household's food database (the existing foods are listed, with their aliases in brackets).
Choose one action per entry:
- existing: the entry is the same shop-bought product as an existing food, interchangeable when shopping (yellow onions = brown onion, spring onions = scallions, mince = minced beef). Set "food" to that food's exact name.
- new: it is a real food or household product with no suitable existing food. Set "food" to a clean generic name: lower case, singular, UK English, typos fixed (brocolli = broccoli), no quantities or brands.
- skip: ONLY if it contains a quantity or unit (2 lbs chicken, 500g flour, 3 tins beans), or is clearly not a food or household product, or is unreadable.
A plain name of a food or product is never a skip, however generic: apples, potatoes, mushrooms, eggs, butter are all real foods. Use existing if one fits, otherwise new with that generic name.
Varieties you would choose between in the shop stay separate: red onions are not brown onions, semi skimmed milk is not skimmed milk. When unsure between existing and new, choose new.
Return every entry via the resolve_items tool, using the index given for each entry."""


def key(s):
    return " ".join(s.strip().lower().split())


def parse_ts(s):
    s = re.sub(r"(\.\d+)", lambda m: (m.group(1) + "000000")[:7], s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


class Mealie:
    def __init__(self, base_url, token):
        self.base, self.token = base_url.rstrip("/"), token

    def req(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(3):
            r = urllib.request.Request(self.base + path, data=data, method=method)
            r.add_header("Authorization", "Bearer " + self.token)
            r.add_header("Accept", "application/json")
            if data is not None:
                r.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(2 * (attempt + 1)); continue
                sys.exit(f"HTTP {e.code} on {method} {path}: {e.read().decode(errors='replace')[:300]}")
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2); continue
                sys.exit(f"Cannot reach {self.base}: {e}")

    def paged(self, path):
        out, page = [], 1
        while True:
            d = self.req("GET", f"{path}?perPage=500&page={page}")
            out += d["items"]
            if page >= d.get("total_pages", 1):
                return out
            page += 1

    def foods(self):
        return self.paged("/api/foods")

    def labels(self):
        return self.paged("/api/groups/labels")

    def shopping_items(self):
        return self.paged("/api/households/shopping/items")

    def create_label(self, name, color):
        return self.req("POST", "/api/groups/labels", {"name": name, "color": color})

    def create_food(self, name, aliases):
        return self.req("POST", "/api/foods", {"name": name, "aliases": [{"name": a} for a in aliases]})

    def put_food(self, food, **changes):
        body = {k: v for k, v in food.items() if k not in ("label", "createdAt", "updatedAt", "recipeCount")}
        body.update(changes)
        self.req("PUT", f"/api/foods/{food['id']}", body)

    def set_label(self, food, label_id):
        self.put_food(food, labelId=label_id)

    def link_item(self, item_id, food_id, label_id):
        cur = self.req("GET", f"/api/households/shopping/items/{item_id}")
        body = {k: v for k, v in cur.items() if k not in ("food", "unit", "label", "display", "createdAt", "updatedAt")}
        body["foodId"] = food_id
        body["labelId"] = label_id
        if body.get("note") and not body.get("recipeReferences"):
            body["note"] = ""            # the food name replaces the typed text
        self.req("PUT", f"/api/households/shopping/items/{item_id}", body)


def claude(tool, names_header, lines, system):
    """Run one forced-tool Claude request. Returns the tool input dict."""
    body = {
        "model": MODEL, "max_tokens": 8192, "system": system,
        "tools": [tool], "tool_choice": {"type": "tool", "name": tool["name"]},
        "messages": [{"role": "user", "content": names_header + "\n".join(lines)}],
    }
    data = json.dumps(body).encode()
    for attempt in range(4):
        r = urllib.request.Request("https://api.anthropic.com/v1/messages", data=data, method="POST")
        r.add_header("x-api-key", ANTHROPIC_API_KEY)
        r.add_header("anthropic-version", "2023-06-01")
        r.add_header("content-type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=180) as resp:
                out = json.loads(resp.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            sys.exit(f"Claude API HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
        except urllib.error.URLError as e:
            if attempt < 3:
                time.sleep(5); continue
            sys.exit(f"Cannot reach Claude API: {e}")
    for block in out.get("content", []):
        if block.get("type") == "tool_use":
            return block["input"]
    return {}


def ask_labels(names):
    """Return {lowercase name: label} for a batch of food names."""
    tool = {
        "name": "assign_labels",
        "description": "Record the supermarket aisle label for each item.",
        "input_schema": {
            "type": "object",
            "properties": {"assignments": {"type": "array", "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "label": {"type": "string", "enum": LABELS}},
                "required": ["i", "label"]}}},
            "required": ["assignments"],
        },
    }
    res = claude(tool, "Items:\n", [f"{i}: {n}" for i, n in enumerate(names)], SYSTEM)
    result = {}
    for a in res.get("assignments", []):
        i, label = a.get("i"), a.get("label")
        if isinstance(i, int) and 0 <= i < len(names) and label in LABELS:
            result[key(names[i])] = label
    return result


def ask_items(texts, foods):
    """Return {lowercase text: {"action", "food"}} for a batch of loose list entries."""
    tool = {
        "name": "resolve_items",
        "description": "Record how each shopping list entry maps to the food database.",
        "input_schema": {
            "type": "object",
            "properties": {"decisions": {"type": "array", "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"},
                               "action": {"type": "string", "enum": ["existing", "new", "skip"]},
                               "food": {"type": "string"}},
                "required": ["i", "action"]}}},
            "required": ["decisions"],
        },
    }
    known = []
    for f in sorted(foods, key=lambda x: x["name"].lower()):
        al = ", ".join(a["name"] for a in f.get("aliases") or [])
        known.append(f"- {f['name']}" + (f" [{al}]" if al else ""))
    header = "Existing foods:\n" + "\n".join(known) + "\n\nList entries:\n"
    res = claude(tool, header, [f"{i}: {t}" for i, t in enumerate(texts)], ITEM_SYSTEM)
    result = {}
    for d in res.get("decisions", []):
        i = d.get("i")
        if isinstance(i, int) and 0 <= i < len(texts) and d.get("action") in ("existing", "new", "skip"):
            result[key(texts[i])] = {"action": d["action"], "food": (d.get("food") or "").strip()}
    return result


def load_json(path, valid=None):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    return {k: v for k, v in data.items() if valid is None or valid(v)}


def save_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(dict(sorted(data.items())), fh, indent=1, ensure_ascii=False)


def forms(s):
    """A name plus its simple plural/singular spellings."""
    s = key(s)
    out = {s, s + "s", s + "es"}
    if s.endswith("es"):
        out.add(s[:-2])
    if s.endswith("s"):
        out.add(s[:-1])
    return out


def food_index(foods):
    """normalised name/plural/alias -> food (first one wins)."""
    idx = {}
    for f in foods:
        for n in [f["name"], f.get("pluralName")] + [a["name"] for a in f.get("aliases") or []]:
            if n:
                idx.setdefault(key(n), f)
    return idx


def plan_links(api, foods):
    """Phase 1. Returns {item id: food} to link. Creates foods/aliases when APPLY. Empty if the list is still busy."""
    items = [i for i in api.shopping_items()
             if not i.get("checked") and not i.get("foodId") and not i.get("recipeReferences")
             and (i.get("note") or "").strip()]
    if not items:
        print("LINK: no loose items.")
        return {}
    newest = max(parse_ts(i["updatedAt"]) for i in items)
    age = (datetime.now(timezone.utc) - newest).total_seconds() / 60
    if age < QUIET_MINUTES and not FORCE:
        print(f"LINK: list changed {age:.0f} min ago, waiting for {QUIET_MINUTES} quiet minutes ({len(items)} loose items).")
        return {}

    idx = food_index(foods)

    def local(text):
        for form in forms(text):
            if form in idx:
                return idx[form]
        return None

    cache = load_json(ITEM_CACHE_FILE, lambda v: isinstance(v, dict) and v.get("action") in ("existing", "new", "skip"))
    todo = sorted({key(i["note"]) for i in items if not local(i["note"]) and key(i["note"]) not in cache})
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        print(f"LINK: asking Claude about {start + 1}-{start + len(batch)} of {len(todo)} entries...")
        cache.update(ask_items(batch, foods))
        save_json(ITEM_CACHE_FILE, cache)

    links, created, count = {}, {}, 0
    for item in sorted(items, key=lambda i: i["note"].lower()):
        text = key(item["note"])
        food = local(text)
        if food:                                   # already matches a name/alias: just link it
            links[item["id"]] = food
            print(f"  {item['note']} -> {food['name']}")
            continue
        d = cache.get(text)
        if not d:
            print(f"  left alone: {item['note']} (no answer from Claude, will retry next run)")
            continue
        if d["action"] == "skip" or not d["food"]:
            print(f"  left alone: {item['note']} (Claude said skip; edit {ITEM_CACHE_FILE} to override)")
            continue
        if LIMIT and count >= LIMIT:
            break
        count += 1
        target = idx.get(key(d["food"])) or created.get(key(d["food"]))
        if d["action"] == "existing" and not target:
            print(f"  left alone: {item['note']} (Claude named unknown food '{d['food']}')")
            continue
        if target:                                 # existing food: record the typed text as an alias
            print(f"  {item['note']} -> {target['name']} (+alias '{text}')")
            if APPLY:
                aliases = [{"name": a["name"]} for a in target.get("aliases") or []] + [{"name": text}]
                api.put_food(target, aliases=aliases)
                target["aliases"] = aliases
            idx[text] = target
        else:                                      # new food
            name = d["food"].strip().lower()
            print(f"  {item['note']} -> NEW food '{name}'")
            if APPLY:
                target = api.create_food(name, [text] if key(name) != text else [])
                idx[key(name)] = idx[text] = created[key(name)] = target
                foods.append(target)
            else:
                target = {"id": None, "name": name, "labelId": None}
        links[item["id"]] = target
    return links


def main():
    if not (MEALIE_URL and MEALIE_TOKEN and ANTHROPIC_API_KEY) or "CHANGE-ME" in (MEALIE_URL + MEALIE_TOKEN + ANTHROPIC_API_KEY):
        sys.exit("Fill in MEALIE_URL, MEALIE_API_KEY and ANTHROPIC_API_KEY in mealie.conf first.")
    print(f"{'APPLYING' if APPLY else 'DRY RUN (nothing will change in Mealie)'}")
    api = Mealie(MEALIE_URL, MEALIE_TOKEN)
    foods = api.foods()

    ids = {}
    for l in api.labels():
        ids.setdefault(l["name"], l["id"])
    for name in LABELS:
        if name in ids:
            continue
        if APPLY:
            ids[name] = api.create_label(name, LABEL_COLORS[name])["id"]
            print(f"  created label: {name}")
        else:
            ids[name] = f"(new {name})"
            print(f"  would create label: {name}")
    if APPLY:   # confirm all 15 really exist in Mealie before carrying on
        now = {l["name"] for l in api.labels()}
        missing = [n for n in LABELS if n not in now]
        if missing:
            sys.exit(f"Mealie is missing labels after creating them: {', '.join(missing)}")
    label_name = {v: k for k, v in ids.items()}
    current_ids = set(ids.values())

    # Phase 1: link loose shopping list items to foods.
    links = plan_links(api, foods) if LINK_ITEMS else {}

    # Phase 2: label foods. Foods on a label not in LABELS count as unlabelled.
    candidates = [f for f in foods if not (ONLY_UNLABELLED and f.get("labelId") in current_ids)]
    cache = load_json(CACHE_FILE, lambda v: v in LABELS)   # drops decisions using retired labels
    todo = sorted({f["name"] for f in candidates if key(f["name"]) not in cache}, key=str.lower)
    print(f"LABEL: {len(foods)} foods, {len(candidates)} to check, {len(todo)} need Claude.")
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        print(f"  asking Claude about {start + 1}-{start + len(batch)} of {len(todo)}...")
        cache.update(ask_labels(batch))
        save_json(CACHE_FILE, cache)

    done = skipped = 0
    unanswered = []
    for f in sorted(candidates, key=lambda x: x["name"].lower()):
        label = cache.get(key(f["name"]))
        if label not in LABELS:
            unanswered.append(f["name"])
            continue
        label_id = ids[label]
        if f.get("labelId") == label_id:
            skipped += 1
            continue
        if LIMIT and done >= LIMIT:
            break
        print(f"  {f['name']} -> {label}")
        if APPLY and f.get("id"):
            api.set_label(f, label_id)
            f["labelId"] = label_id
        done += 1
    print(f"{'Updated' if APPLY else 'Would update'} {done} foods, already correct {skipped}, no answer {len(unanswered)}.")
    for n in unanswered:
        print(f"  no answer (re-run to retry): {n}")

    # Phase 3: point list items at their food and its aisle label.
    by_id = {f["id"]: f for f in foods if f.get("id")}
    synced = 0
    for item in api.shopping_items():
        if item.get("checked"):
            continue
        food = links.get(item["id"]) or by_id.get(item.get("foodId"))
        if not food:
            continue
        want = food.get("labelId") if food.get("labelId") in current_ids else None
        linking = item["id"] in links
        if not linking and (not want or item.get("labelId") == want):
            continue
        if linking and food.get("id") is None:
            continue                              # new food in a dry run
        if LIMIT and synced >= LIMIT:
            break
        print(f"  list item '{item.get('note') or item.get('display')}': "
              f"{'link to ' + food['name'] + ', ' if linking else ''}label {label_name.get(want, 'none')}")
        if APPLY:
            api.link_item(item["id"], food["id"], want)
        synced += 1
    print(f"SYNC: {'updated' if APPLY else 'would update'} {synced} list items.")


def run_locked():
    os.makedirs(os.path.dirname(LOCK_FILE) or ".", exist_ok=True)
    try:
        if time.time() - os.path.getmtime(LOCK_FILE) < 600:
            sys.exit("Another run is in progress, skipping.")
    except FileNotFoundError:
        pass
    open(LOCK_FILE, "w").close()
    try:
        main()
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    run_locked()
