#!/usr/bin/env bash
# One full collection: probe, retry the failures, derive.
#
# Used by hand and, later, by the scheduled workflow -- the same chain either
# way, so a scheduled run cannot drift from a manual one.
#
# Deliberately not `set -e`. A run exits non-zero when it is degraded, and the
# retry pass is precisely the remedy for that, so bailing on the first non-zero
# exit would skip the step most likely to fix it.
#
# Never pipe the run through `tail` or `head`: the pipeline's exit status is
# the last command's, which silently turns a degraded run into a success. That
# mistake hid a degraded run once already.
set -uo pipefail

PANEL="${1:-panel-01}"
shift || true

cd "$(dirname "$(readlink -f "$0")")" || exit 1

export OBSERVATORY_CONTACT="${OBSERVATORY_CONTACT:-https://florianhoehn.github.io/robots-observatory/bot/}"
export OBSERVATORY_VANTAGE="${OBSERVATORY_VANTAGE:-residential-gb-ni}"

echo "=== $(date -Is)  panel=$PANEL  vantage=$OBSERVATORY_VANTAGE"

uv run observatory.py run --panel "$PANEL" "$@"
run_rc=$?
echo "=== $(date -Is)  run exited $run_rc"

if [ "$run_rc" -ne 0 ] && [ ! -s "$(ls -d data/runs/"$PANEL"/*/records.jsonl 2>/dev/null | tail -1)" ]; then
  echo "=== run produced no records; stopping before retry"
  exit "$run_rc"
fi

# A transient timeout recorded as a policy fact is a wrong answer, so every
# failed host gets one more attempt before its failure is treated as data.
uv run observatory.py retry --panel "$PANEL"
echo "=== $(date -Is)  retry exited $?"

uv run observatory.py derive --panel "$PANEL"
derive_rc=$?
echo "=== $(date -Is)  derive exited $derive_rc"
exit "$derive_rc"
