#!/bin/bash
# Export every recipe's slug, name, time and ingredients to recipes_export.jsonl.
# Read-only: changes nothing in Mealie. Settings come from mealie.conf. Requires curl, jq.
CONF="${MEALIE_CONF:-/mnt/user/appdata/mealie-scripts/mealie.conf}"
source "$CONF" || { echo "Cannot read $CONF"; exit 1; }
DIR="${DATA_DIR:-$(dirname "$CONF")}"
OUT="$DIR/recipes_export.jsonl"
: > "$OUT"

page=1
while :; do
  resp=$(curl -s -H "Authorization: Bearer $MEALIE_API_KEY" "$MEALIE_URL/api/recipes?page=$page&perPage=200")
  slugs=$(echo "$resp" | jq -r '.items[]?.slug')
  [ -z "$slugs" ] && break
  for s in $slugs; do
    curl -s -H "Authorization: Bearer $MEALIE_API_KEY" "$MEALIE_URL/api/recipes/$s" \
      | jq -c '{slug, name, totalTime, ingredients: [.recipeIngredient[]? | (.display // .originalText // .note // "")]}' >> "$OUT"
  done
  echo "page $page done"
  page=$((page+1))
done
echo "Exported $(wc -l < "$OUT") recipes to $OUT"
