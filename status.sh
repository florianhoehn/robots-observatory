#!/usr/bin/env bash
# Progress and early findings for a run in flight, from the records written so
# far. Read-only: it touches no host and never writes.
#
#   ./status.sh [panel] [run-id]
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")" || exit 1

BRIEF=0
if [ "${1:-}" = "--brief" ]; then BRIEF=1; shift; fi
PANEL="${1:-panel-02}"
RUN="${2:-}"

if [ -z "$RUN" ]; then
  RUN=$(ls -dt data/runs/"$PANEL"/*/ 2>/dev/null | head -1)
else
  RUN="data/runs/$PANEL/$RUN/"
fi
[ -z "$RUN" ] && { echo "no run found for $PANEL"; exit 1; }
# Records are sharded by rank band; older runs have a single records.jsonl.
REC_GLOB="${RUN}records/*.jsonl"
# shellcheck disable=SC2086
if ! compgen -G "$REC_GLOB" > /dev/null; then
  REC_GLOB="${RUN}records.jsonl"
  [ -s "$REC_GLOB" ] || { echo "no records yet in ${RUN}"; exit 1; }
fi

PANEL_N=$(( $(wc -l < "panel/$PANEL.csv") - 1 ))

python3 - "$REC_GLOB" "$PANEL_N" "$RUN" "$BRIEF" "${PREV:-0}" <<'PY'
import collections, json, sys, os, time

import glob

rec_glob, panel_n, run_dir = sys.argv[1], int(sys.argv[2]), sys.argv[3]
brief = len(sys.argv) > 4 and sys.argv[4] == "1"
prev = int(sys.argv[5]) if len(sys.argv) > 5 else 0
paths = sorted(glob.glob(rec_glob))
recs = []
for rec_path in paths:
    for ln in open(rec_path, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            recs.append(json.loads(ln))
        except ValueError:
            pass  # a torn final line while the run is mid-write

n = len(recs)
pct = 100 * n / panel_n if panel_n else 0

if brief:
    # One line, for a watcher. Everything the long form reports, compressed.
    import datetime
    errs = sum(1 for r in recs if r.get("errors"))
    dup = n - len({r["host"] for r in recs})
    pol = lambda r, k, d=None: r.get("policy", {}).get(k, d)
    pres = [r for r in recs if pol(r, "file_state") == "present"]
    named = sum(1 for r in pres if pol(r, "ai_tokens_named"))
    def ca(r):
        c = collections.Counter(v.get("sha256") for v in (r.get("wellknown") or {}).values()
                                if v.get("file_state") == "present" and v.get("sha256"))
        return {h for h, x in c.items() if x > 1}
    wk = collections.Counter()
    for r in recs:
        bad = ca(r)
        for k, v in (r.get("wellknown") or {}).items():
            if v.get("file_state") == "present" and v.get("sha256") not in bad:
                wk[k] += 1
    bits = [f"{n}/{panel_n} ({pct:.0f}%)", f"err {100*errs/max(n,1):.0f}%"]
    if dup:
        bits.append(f"DUPES {dup}")
    if pres:
        bits.append(f"namesAI {100*named/len(pres):.0f}%")
    for k, lab in (("llms_txt", "llms"), ("ads_txt", "ads"), ("security_txt", "sec")):
        bits.append(f"{lab} {100*wk[k]/max(n,1):.1f}%")
    bits.append(f"AGENTS {wk['agent_card']}+{wk['ai_catalog']}+{wk['agent_card_legacy']}")
    if prev and n > prev:
        rate = (n - prev) / 600
        eta = datetime.datetime.now() + datetime.timedelta(seconds=(panel_n - n) / rate)
        bits.append(f"{rate:.2f}/s ETA {eta:%H:%M}")
    print(" | ".join(bits))
    raise SystemExit(0)
age = time.time() - max(os.path.getmtime(p) for p in paths)
print(f"{n:,} / {panel_n:,} hosts  ({pct:.1f}%)   last write {age:.0f}s ago")

if n:
    errs = sum(1 for r in recs if r.get("errors"))
    print(f"errors {errs} ({100*errs/n:.1f}%)" + ("   ** above the 5% budget **" if errs / n > .05 else ""))

def pol(r, k, d=None):
    return r.get("policy", {}).get(k, d)

present = [r for r in recs if pol(r, "file_state") == "present"]
if present:
    named = sum(1 for r in present if pol(r, "ai_tokens_named"))
    print(f"\nrobots.txt readable on {len(present):,}; {named:,} name an AI crawler "
          f"({100*named/len(present):.1f}%)")

# A host answering every unknown path with one body has a catch-all, not
# files. cloudflare.net returns "Invalid key" for five of them.
def catch_all(r):
    c = collections.Counter(v.get("sha256") for v in (r.get("wellknown") or {}).values()
                            if v.get("file_state") == "present" and v.get("sha256"))
    return {h for h, n in c.items() if n > 1}

wk = collections.Counter()
seen = collections.Counter()
bogus = 0
for r in recs:
    ca = catch_all(r)
    if ca:
        bogus += 1
    for k, v in (r.get("wellknown") or {}).items():
        seen[k] += 1
        if v.get("file_state") == "present" and v.get("sha256") not in ca:
            wk[k] += 1
if bogus:
    print(f"\n({bogus} host(s) with a catch-all response, discounted)")
if seen:
    print("\nwell-known files found:")
    for k in sorted(seen, key=lambda k: -wk[k]):
        c = wk[k]
        print(f"  {k:20} {c:6,}  {100*c/seen[k]:5.2f}%")

states = collections.Counter(pol(r, "file_state", "?") for r in recs)
print("\nrobots.txt states: " + "  ".join(
    f"{k}={v}" for k, v in states.most_common(6)))

# the two signals worth watching live
rsl = sum(1 for r in recs if pol(r, "rsl_license_refs"))
varies = sum(1 for r in recs if r.get("policy_varies_by_identity"))
print(f"\nlicensing/pay signals: {rsl}      policy varies by identity: {varies}")

toks = collections.Counter()
for r in present:
    for t in pol(r, "ai_tokens_named", []) or []:
        toks[t] += 1
if toks:
    top = "  ".join(f"{t}:{c}" for t, c in toks.most_common(6))
    print(f"\nmost-named crawlers: {top}")
PY
