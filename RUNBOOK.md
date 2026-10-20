# Runbook

How to operate a collection, and what to do when one goes wrong.

## Start a run

    ./run.sh panel-02                 # probe -> retry failures -> derive
    ./run.sh panel-01                 # the weekly 463-host cohort

`run.sh` retries with `--resume` up to six times with exponential backoff.
Resume is idempotent: it reads the records already written and skips those
hosts, so re-running after a dropped connection or a suspended laptop costs
minutes rather than the night. It never pipes the run through `tail` — a
pipeline's exit status is the last command's, which turns a degraded run into
an apparent success.

## Watch one in flight

    ./status.sh panel-02              # progress + early findings, read-only

Reports hosts done, error rate against the 5% budget, well-known hit rates,
robots.txt state breakdown, and the most-named crawlers. Tolerates a torn
final line while the run is mid-write.

## Scheduled runs

    systemctl --user list-timers robots-observatory-panel02.timer
    journalctl --user -u robots-observatory-panel02.service -f
    tail -f ~/.local/state/robots-observatory/panel-02.log

## When something is wrong

| Symptom | What it means | Do |
|---|---|---|
| `degraded (error_rate_…)` | over the 5% budget | `./status.sh`, then `observatory.py retry` — transient timeouts usually clear |
| `FILTERING RESOLVER DETECTED` | the vantage has DNS ad-blocking | exclude those hosts with a published reason, or run elsewhere. Never `--allow-unresolvable` silently |
| `N PANEL HOST(S) DO NOT RESOLVE` | panel contains hosts this vantage can't see | same |
| `run … already exists` | a finished run of that id | `--resume` to continue it, `--run-id` to name a new one, `--force` only deliberately |
| Pages shows stale content | check `gh api repos/OWNER/REPO/pages/builds/latest --jq .status` | `errored` ≠ slow. `docs/.nojekyll` must exist |
| Local preview | `cd docs && python3 -m http.server 8899` | http://127.0.0.1:8899/ |

## Re-deriving

The archive holds every response body and every header, so a new question is
answered from stored responses rather than by crawling again:

    observatory.py reparse --panel panel-02     # re-derive policy from blobs
    observatory.py reshard --panel panel-02     # split legacy records.jsonl by band
    observatory.py derive  --panel panel-02
    observatory.py corpus  --file llms_txt      # every stored llms.txt
    observatory.py diff HOST RUN_A RUN_B        # one host, between runs

The one thing no archive recovers is a request never made. Adding a path to
`WELLKNOWN` only ever applies to future runs.

## Before trusting any number

Every headline figure in this project has been wrong on first measurement:
a 10x inflated terms-adoption rate (bare 200s counted as files), an identity
-variance finding that was nonces in error pages, a run contaminated by the
vantage's DNS filtering, an 18% "unresolvable" that was really 2%, and a
mechanical screen that got worse when made more precise. None were caught by
tests. All were caught by finding a number implausible and checking it.

Check the surprising ones by hand before they go on a page.

### Two that only appear when the panel changes

**Never divide the percentages.** `findings.json` rounds `pct` to one decimal.
The page computed the documentation-to-price ratio as `llms.pct / pay.pct`,
which at 11.3/0.1 printed **113** where the true ratio is 1837/14 = **131**. At
small denominators a rounded percentage carries almost no precision. Derive
every ratio from the counts.

**A hand-picked example is a measurement too, and it expires.** The page named
`rakuten.co.jp` as its example of identity-dependent *policy*, true on panel-01.
On panel-02 that host is access variance instead -- the exact distinction the
section exists to draw, illustrated by a counter-example. When the panel
changes, re-check every named host, not just the aggregates.

The same applies to prose: "a third have gone through their rules" was
panel-01's 32.8% still sitting above panel-02's 17.2%, and Method, the download
links and the conduct pages all described the previous probe set. Numbers
written into sentences do not get recomputed. Grep the page for digits after
every panel change.

## Where a run's records live

One file per rank band, under `data/runs/<panel>/<run-id>/records/`:

    rank-00001-01000.jsonl   rank-05001-10000.jsonl
    rank-01001-02500.jsonl   rank-10001-20000.jsonl
    rank-02501-05000.jsonl   rank-beyond.jsonl / rank-unknown.jsonl (if any)

The bands are `RANK_BANDS`, the same ones the published rank facets use, so the
file you download is exactly the band whose figure you are checking. A single
83 MB `records.jsonl` was the previous layout; it is still read if present, so
nothing needs migrating to stay readable. `reshard` performs the split and
verifies the round trip before unlinking the original.

**Bodies are not in these files, and not in git.** A record carries status,
timings, headers and a `sha256`; `data/blobs/` holds the bodies and is
local-only. `reparse` therefore works only on a machine that has them -- and
losing that directory means a question about body content can no longer be
answered without re-crawling, which is exactly what the archive exists to
avoid. Back it up separately.

### After any change to the records layout

Prove the aggregates did not move. Resharding only relocates rows, so the
records compared as a sorted set must be identical before and after:

    git show HEAD:data/runs/<panel>/<date>/records.jsonl | sort | sha256sum
    cat data/runs/<panel>/<date>/records/*.jsonl | sort | sha256sum

Equal hashes mean any derivation is unchanged. If `derive` then reports
different findings, the cause is a change in the derivation code, not the
split -- which is how the panel-01 catch-all discount surfaced.
