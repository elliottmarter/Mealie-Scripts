#!/bin/bash
# Delete recipes listed (one slug per line) in delete_list.txt. Dry run unless APPLY=1 in mealie.conf.
# Settings come from mealie.conf; data files live in DATA_DIR. Needs delete_list.txt. Requires curl.
CONF="${MEALIE_CONF:-/mnt/user/appdata/mealie-scripts/mealie.conf}"
source "$CONF" || { echo "Cannot read $CONF"; exit 1; }
DIR="${DATA_DIR:-$(dirname "$CONF")}"
APPLY="${APPLY:-0}"
LIST="$DIR/delete_list.txt"
LOG="$DIR/delete_log.txt"
[ -f "$LIST" ] || { echo "Missing $LIST"; exit 1; }

ok=0; gone=0; fail=0; : > "$LOG"
while read -r slug; do
  [ -z "$slug" ] && continue
  if [ "$APPLY" != "1" ]; then echo "would delete: $slug"; continue; fi
  code=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE -H "Authorization: Bearer $MEALIE_API_KEY" "$MEALIE_URL/api/recipes/$slug")
  case $code in
    200) ok=$((ok+1));;
    404) gone=$((gone+1));;
    *) fail=$((fail+1)); echo "FAILED $code $slug" | tee -a "$LOG";;
  esac
done < "$LIST"
echo "deleted=$ok already-gone=$gone failed=$fail (apply=$APPLY)"
