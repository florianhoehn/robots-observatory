#!/usr/bin/env bash
# One full collection: probe, retry the failures, derive.
#
# Used by hand and by the scheduled timer -- the same chain either way, so a
# scheduled run cannot drift from a manual one.
#
# Deliberately not `set -e`. A run exits non-zero when it is degraded, and the
# retry pass is precisely the remedy for that, so bailing on the first non-zero
# exit would skip the step most likely to fix it.
#
# Never pipe the run through `tail` or `head`: a pipeline's exit status is the
# last command's, which silently turns a degraded run into a success. That
# mistake hid a degraded run once already.
set -uo pipefail

PANEL="${1:-panel-01}"
shift || true

cd "$(dirname "$(readlink -f "$0")")" || exit 1

export OBSERVATORY_CONTACT="${OBSERVATORY_CONTACT:-https://florianhoehn.github.io/robots-observatory/bot/}"
export OBSERVATORY_VANTAGE="${OBSERVATORY_VANTAGE:-residential-gb-ni}"

# An eight-hour unattended pass will meet a dropped connection or a suspended
# laptop sooner or later. Resume is idempotent -- it skips hosts already in the
# records file -- so retrying is strictly better than losing the night.
MAX_ATTEMPTS="${OBSERVATORY_MAX_ATTEMPTS:-6}"
BACKOFF=60

log() { echo "=== $(date -Is)  $*"; }

log "panel=$PANEL vantage=$OBSERVATORY_VANTAGE attempts<=$MAX_ATTEMPTS"

attempt=1
run_rc=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  if [ "$attempt" -eq 1 ]; then
    uv run observatory.py run --panel "$PANEL" "$@"
  else
    log "attempt $attempt: resuming"
    uv run observatory.py run --panel "$PANEL" --resume "$@"
  fi
  run_rc=$?
  log "attempt $attempt exited $run_rc"

  # 0 = complete. A degraded run also stops here: its remedy is the retry
  # pass below, not another full sweep.
  if [ "$run_rc" -eq 0 ]; then break; fi

  # Nothing left to resume? Compare records against the panel. This used to
  # invoke the run itself inside a `grep -q`, which does not test a condition
  # -- it starts another full collection. Six retries meant six concurrent
  # runs appending to one file: 452 hosts recorded twice and double the
  # requests promised in CONDUCT.md.
  latest=$(ls -dt data/runs/"$PANEL"/*/ 2>/dev/null | head -1)
  if [ -n "$latest" ] && [ -s "${latest}records.jsonl" ]; then
    done_n=$(awk 'END{print NR}' "${latest}records.jsonl")
    panel_n=$(( $(awk 'END{print NR}' "panel/$PANEL.csv") - 1 ))
    if [ "$done_n" -ge "$panel_n" ]; then
      log "all $panel_n hosts recorded"
      run_rc=0
      break
    fi
  fi

  attempt=$((attempt + 1))
  [ "$attempt" -le "$MAX_ATTEMPTS" ] && { log "backing off ${BACKOFF}s"; sleep "$BACKOFF"; BACKOFF=$((BACKOFF * 2)); }
done

latest=$(ls -dt data/runs/"$PANEL"/*/ 2>/dev/null | head -1)
if [ -z "$latest" ] || [ ! -s "${latest}records.jsonl" ]; then
  log "no records produced; stopping before retry"
  exit 1
fi

# A transient timeout recorded as a policy fact is a wrong answer, so every
# failed host gets one more attempt before its failure is treated as data.
uv run observatory.py retry --panel "$PANEL"
log "retry exited $?"

uv run observatory.py derive --panel "$PANEL"
derive_rc=$?
log "derive exited $derive_rc"
log "done"
exit "$derive_rc"
