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
