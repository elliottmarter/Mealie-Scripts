# Mealie Scripts

Personal [Mealie](https://mealie.io) maintenance scripts, designed to run on
**Unraid** through the **User Scripts** plugin. They are vibe coded and tested
only against my own Mealie instance. **Use at your own risk.** Take a Mealie
backup before running anything that writes.

All scripts share one settings file, `mealie.conf`, so URLs and API keys live
in one place and are never stored in the scripts themselves.

## Scripts

| Script | Language | What it does | Writes to Mealie? |
| --- | --- | --- | --- |
| `backup.sh` | bash | Creates a Mealie backup, then deletes backups older than `RETENTION_DAYS`. | Yes (creates and prunes backups) |
| `export_recipes.sh` | bash | Exports every recipe's slug, name, total time and ingredients to `recipes_export.jsonl`. | No |
| `apply_tags.sh` | bash | Applies tags from `tag_plan.txt` (creates missing tags, saves each recipe's old tags to `tags_backup.jsonl`). | Only with `APPLY=1` |
| `delete_recipes.sh` | bash | Deletes the recipe slugs listed in `delete_list.txt`. | Only with `APPLY=1` |
| `bulk_ingredient_parser.py` | Python | Uses Mealie's NLP parser to turn plain-text ingredients into quantity / unit / food, converts US units to metric, and tags processed recipes `Parsed`. Works on a random batch each run. | Only with `APPLY=1` |
| `claude_food_labels.py` | Python | Uses Claude to (1) link loose shopping-list items to foods (adding aliases or creating foods), (2) give every food an aisle label (15 supermarket-order labels), (3) copy those labels onto list items. Designed to run every 5 minutes. | Only with `APPLY=1` |
| `delete_checked_shopping_items.py` | Python | Deletes all checked items from every shopping list. | Only with `APPLY=1` |

"Only with `APPLY=1`" means the script is a dry run by default and prints what
it would do. Set `APPLY=1` in `mealie.conf` to make real changes, and set it
back to `0` afterwards.

## Setup on Unraid

### 1. Prerequisites

- **User Scripts** plugin (Apps tab > search "User Scripts").
- `jq` for the bash scripts (for example via the NerdTools plugin). `curl` is
  already present.
- **Python 3** for the `.py` scripts (only the standard library is used).
- A Mealie API token: Mealie > User Profile > **API Tokens**.
- `claude_food_labels.py` only: an Anthropic API key from
  console.anthropic.com.

### 2. Put the files on the server

```bash
mkdir -p /mnt/user/appdata/mealie-scripts
```

Copy every script and `mealie.conf.example` from this repo into that folder
(clone the repo onto the server, use an SMB share, or `scp`). Then:

```bash
cd /mnt/user/appdata/mealie-scripts
cp mealie.conf.example mealie.conf
chmod 600 mealie.conf
nano mealie.conf
chmod +x *.sh *.py
```

### 3. Edit `mealie.conf`

| Setting | Used by | Meaning |
| --- | --- | --- |
| `MEALIE_URL` | all | Base URL, no trailing slash and no `/api` (for example `http://192.168.1.50:9925`). |
| `MEALIE_API_KEY` | all | Your Mealie API token. |
| `ANTHROPIC_API_KEY` | `claude_food_labels.py` | Anthropic API key. |
| `APPLY` | scripts that write data | `0` = dry run, `1` = make real changes. |
| `RETENTION_DAYS` | `backup.sh` | Delete backups older than this many days (default 30). |
| `DATA_DIR` | all | Folder for plan files, logs, exports and caches. Defaults to the folder holding `mealie.conf`. |

To keep `mealie.conf` somewhere else, set the `MEALIE_CONF` environment
variable to its full path in the launcher.

### 4. Add each script to User Scripts

User Scripts copies a script's text before running it, so use a one-line
**launcher** that calls the real file by its full path.

1. **Settings > User Scripts > Add New Script** and name it (for example
   `mealie-backup`).
2. Click the gear > **Edit Script** and replace the contents:

   ```bash
   #!/bin/bash
   bash /mnt/user/appdata/mealie-scripts/backup.sh
   ```

   For Python scripts:

   ```bash
   #!/bin/bash
   python3 /mnt/user/appdata/mealie-scripts/claude_food_labels.py
   ```

3. Set the schedule (see below), then **Apply**. Use **Run Script** or **Run in
   Background** to test, and **View Log** to read the output.

Suggested schedules:

| Script | Schedule |
| --- | --- |
| `backup.sh` | Scheduled Daily (or custom cron `0 3 * * *`) |
| `claude_food_labels.py` | Custom cron `*/5 * * * *` (only after testing, see below) |
| `delete_checked_shopping_items.py` | Optional: Scheduled Weekly, or run by hand |
| `export_recipes.sh`, `apply_tags.sh`, `delete_recipes.sh`, `bulk_ingredient_parser.py` | Schedule Disabled; run by hand |

## Using the scripts

### Always dry run first

Leave `APPLY=0`, run the script, and read the log. When it looks right, set
`APPLY=1`, run again, then set `APPLY=0` again unless the script is scheduled
(for example the label script) and you want it to keep applying.

### `apply_tags.sh`

Create `tag_plan.txt` in `DATA_DIR`, one recipe per line, tags separated by `;`:

```text
chicken-curry|Dinner;Curry;Quick
banana-bread|Baking;Snack
```

This **replaces** each listed recipe's tags with exactly those listed. Old tags
are saved to `tags_backup.jsonl`; failures go to `tag_log.txt`. Use
`export_recipes.sh` to find slugs.

### `delete_recipes.sh`

Create `delete_list.txt` in `DATA_DIR` with one recipe slug per line. Dry run
prints `would delete: <slug>`. Deletion is permanent, so run `backup.sh` first.

### `bulk_ingredient_parser.py`

Edit `COUNT` (recipes per run) near the top of the script. Recommended order:
backup, dry run a few times, live with `COUNT = 1` and check that recipe in
Mealie, then raise `COUNT` gradually. It creates new foods and units wherever
the parser finds no match, and those persist. Filter by the `Parsed` tag to see
progress.

### `claude_food_labels.py`

1. Run with `APPLY=0`. Decisions are printed and cached in `DATA_DIR`
   (`ai_labels.json` for foods, `ai_items.json` for list items). Edit those
   files to override a decision.
2. Set `APPLY=1` in `mealie.conf` and `LIMIT = 1` in the script, run once, and
   check Mealie.
3. Set `LIMIT = 0` and schedule it every 5 minutes. Cached decisions are
   reused, so only new text costs an API call. Loose list items are only
   linked once the list has been quiet for `QUIET_MINUTES` (default 15).

It creates 15 numbered labels (`01 Fruit & Veg` through `15 Drinks`) if they do
not exist. Reorder them in Mealie's shopping list view to match your shop.

### `delete_checked_shopping_items.py`

Dry run lists every checked item per list. With `APPLY=1` it deletes them in
batches of 50.

## Troubleshooting

- **`Cannot read .../mealie.conf`**: the file is missing or `MEALIE_CONF` points
  elsewhere.
- **`Could not read tags from Mealie`** or HTTP 401: wrong `MEALIE_URL` or an
  invalid API token.
- **`jq: command not found`**: install `jq` on the server.
- **`FAILED 404` for a recipe**: the slug does not exist; check it against
  `recipes_export.jsonl`.
- **`Another run is in progress, skipping.`** (label script): a previous run
  left `run.lock` in `DATA_DIR`; it is ignored after 10 minutes.

## Security

`mealie.conf` holds your API keys. It is listed in `.gitignore`; never commit
it or paste it into issues. Create a dedicated Mealie token for these scripts
and revoke it if you stop using them.
