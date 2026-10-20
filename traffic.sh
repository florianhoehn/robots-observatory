#!/usr/bin/env bash
# Snapshot the traffic numbers GitHub will otherwise delete.
#
#   ./traffic.sh          append today's figures to data/traffic/
#   ./traffic.sh --show   print what has been collected so far
#
# GitHub's traffic API is a rolling 14-day window. Nothing warns you when a
# day falls off the end; it is simply gone, and there is no way to recover it.
# A launch spike is invisible a fortnight later unless something wrote it down.
# So this writes it down.
#
# What it covers, and what it does not: these are views of the *repository* on
# github.com, not of the Pages site. GitHub Pages produces no access logs at
# all, so visits to the published pages are counted client-side by GoatCounter
# (see the snippet in docs/*.html) and are not visible here.
#
# Read-only against the API, append-only on disk. Safe to run daily, and safe
# to run twice in a day: the second run replaces that day's row rather than
# duplicating it.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

REPO="${OBSERVATORY_REPO:-florianhoehn/robots-observatory}"
OUT="data/traffic"
mkdir -p "$OUT"

if [ "${1:-}" = "--show" ]; then
  if [ ! -s "$OUT/daily.csv" ]; then
    echo "nothing collected yet; run ./traffic.sh first"
    exit 0
  fi
  echo "== daily (deduplicated, oldest first) =="
  column -s, -t < "$OUT/daily.csv"
  if [ -s "$OUT/referrers.csv" ]; then
    echo
    echo "== referrers, most recent snapshot =="
    awk -F, -v last="$(tail -1 "$OUT/referrers.csv" | cut -d, -f1)" \
        'NR==1 || $1==last' "$OUT/referrers.csv" | column -s, -t
  fi
  exit 0
fi

command -v gh >/dev/null || { echo "gh CLI not found"; exit 1; }

# --- daily views and clones -------------------------------------------------
# The API returns up to 14 daily rows each time. Merging on the date key means
# running this weekly still produces an unbroken series, and running it twice
# in a day does not double-count.
tmp=$(mktemp)
gh api "repos/$REPO/traffic/views" --jq \
  '.views[] | [(.timestamp|split("T")[0]), "views", .count, .uniques] | @csv' \
  > "$tmp" 2>/dev/null || { echo "could not read views for $REPO"; rm -f "$tmp"; exit 1; }
gh api "repos/$REPO/traffic/clones" --jq \
  '.clones[] | [(.timestamp|split("T")[0]), "clones", .count, .uniques] | @csv' \
  >> "$tmp" 2>/dev/null

header="date,metric,count,uniques"
[ -s "$OUT/daily.csv" ] || echo "$header" > "$OUT/daily.csv"

# keep whichever row is newest for a given (date, metric), then sort by date
{ tail -n +2 "$OUT/daily.csv"; cat "$tmp"; } \
  | sed 's/"//g' \
  | awk -F, '{ key = $1 "," $2; row[key] = $0 } END { for (k in row) print row[k] }' \
  | sort -t, -k1,1 -k2,2 > "$tmp.merged"
{ echo "$header"; cat "$tmp.merged"; } > "$OUT/daily.csv"

# --- referrers and paths, stamped with the day they were read ---------------
# These are 14-day totals rather than daily counts, so they are stored as
# dated snapshots and should be read as "as of this date", never summed.
today=$(date -u +%F)
# Deduplicated on (snapshot, key) like the daily rows, so running this more
# than once a day replaces today's snapshot instead of stacking copies of it.
snapshot_merge() {   # $1 = file, $2 = header, $3 = key column index
  local f="$1" head="$2" keycol="$3" new="$4"
  [ -s "$f" ] || echo "$head" > "$f"
  { tail -n +2 "$f"; cat "$new"; } \
    | awk -F, -v kc="$keycol" '{ key = $1 "," $kc; row[key] = $0 }
                               END { for (k in row) print row[k] }' \
    | sort -t, -k1,1 -k2,2 > "$new.m"
  { echo "$head"; cat "$new.m"; } > "$f"
  rm -f "$new" "$new.m"
}

rtmp=$(mktemp)
gh api "repos/$REPO/traffic/popular/referrers" --jq \
  ".[] | [\"$today\", .referrer, .count, .uniques] | @csv" 2>/dev/null \
  | sed 's/"//g' > "$rtmp"
snapshot_merge "$OUT/referrers.csv" "snapshot,referrer,count,uniques" 2 "$rtmp"

ptmp=$(mktemp)
gh api "repos/$REPO/traffic/popular/paths" --jq \
  ".[] | [\"$today\", .path, .title, .count, .uniques] | @csv" 2>/dev/null \
  | sed 's/"//g' | tr -d '\r' > "$ptmp"
snapshot_merge "$OUT/paths.csv" "snapshot,path,title,count,uniques" 2 "$ptmp"

rm -f "$tmp" "$tmp.merged"

days=$(( $(wc -l < "$OUT/daily.csv") - 1 ))
echo "snapshot $today: $days dated rows in $OUT/daily.csv"
echo "  views  $(gh api "repos/$REPO/traffic/views"  --jq '"\(.count) total, \(.uniques) unique (14d)"')"
echo "  clones $(gh api "repos/$REPO/traffic/clones" --jq '"\(.count) total, \(.uniques) unique (14d)"')"
echo
echo "Repository traffic only. Visits to the published pages are counted"
echo "client-side; GitHub Pages produces no server logs."
