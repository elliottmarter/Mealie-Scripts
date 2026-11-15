#!/bin/bash
# Apply tags from tag_plan.txt to Mealie recipes. Dry run unless APPLY=1 in mealie.conf.
# Settings come from mealie.conf; data files live in DATA_DIR. Needs tag_plan.txt (lines: slug|Tag1;Tag2;Tag3). Requires curl, jq.
CONF="${MEALIE_CONF:-/mnt/user/appdata/mealie-scripts/mealie.conf}"
source "$CONF" || { echo "Cannot read $CONF"; exit 1; }
DIR="${DATA_DIR:-$(dirname "$CONF")}"
APPLY="${APPLY:-0}"
PLAN="$DIR/tag_plan.txt"
BACKUP="$DIR/tags_backup.jsonl"
LOG="$DIR/tag_log.txt"

api() { # method path [json-body]
  if [ -n "$3" ]; then
    curl -s -X "$1" -H "Authorization: Bearer $MEALIE_API_KEY" -H "Content-Type: application/json" -d "$3" "$MEALIE_URL$2"
  else
    curl -s -X "$1" -H "Authorization: Bearer $MEALIE_API_KEY" "$MEALIE_URL$2"
  fi
}

[ -f "$PLAN" ] || { echo "Missing $PLAN"; exit 1; }
TAGS=$(api GET "/api/organizers/tags?page=1&perPage=1000")
echo "$TAGS" | jq -e '.items' >/dev/null 2>&1 || { echo "Could not read tags from Mealie. Check mealie.conf."; exit 1; }

NEEDED=$(cut -d'|' -f2 "$PLAN" | tr ';' '\n' | sort -u)
MISSING=""
while IFS= read -r name; do
  [ -z "$name" ] && continue
  echo "$TAGS" | jq -e --arg n "$name" '.items[] | select((.name|ascii_downcase)==($n|ascii_downcase))' >/dev/null || MISSING="$MISSING$name"$'\n'
done <<< "$NEEDED"
echo "$(grep -c . "$PLAN") recipes. New tags to create:"; printf '%s' "$MISSING" | sed 's/^/  /'

if [ "$APPLY" != "1" ]; then echo "Dry run only. Set APPLY=1 in mealie.conf to make changes."; exit 0; fi

while IFS= read -r name; do
  [ -z "$name" ] && continue
  api POST /api/organizers/tags "$(jq -nc --arg n "$name" '{name:$n}')" >/dev/null
done <<< "$MISSING"
TAGS=$(api GET "/api/organizers/tags?page=1&perPage=1000")

: > "$LOG"; OK=0; FAIL=0
while IFS='|' read -r slug taglist; do
  [ -z "$slug" ] && continue
  api GET "/api/recipes/$slug" | jq -c --arg s "$slug" '{slug:$s, tags:.tags}' >> "$BACKUP"
  body=$(echo "$TAGS" | jq -c --arg l "$taglist" '($l|split(";")|map(ascii_downcase)) as $w | {tags:[.items[]|select((.name|ascii_downcase) as $n|$w|index($n))|{id,name,slug}]}')
  code=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH -H "Authorization: Bearer $MEALIE_API_KEY" -H "Content-Type: application/json" -d "$body" "$MEALIE_URL/api/recipes/$slug")
  if [ "$code" = "200" ]; then OK=$((OK+1)); else FAIL=$((FAIL+1)); echo "FAILED $code $slug" >> "$LOG"; fi
done < "$PLAN"
echo "updated=$OK failed=$FAIL (failures in $LOG, previous tags in $BACKUP)"
