# Robots Observatory

How does the web *declare* its policy toward AI agents — and does that
declaration change depending on who asks?

A frozen panel of 480 real websites, drawn from the Tranco top 1000 and
screened down to hosts that are actually websites. Every week we read each
host's policy surface — `robots.txt` plus ten well-known paths — under two
requester identities, and publish the result.

**Policy only.** This project reads the files the web publishes for machines to
read. It never fetches a page, so it measures what sites *say*, not what they
*enforce*. Those are different datasets; this is the first one. See
[CONDUCT.md](CONDUCT.md).

## Status

Panel frozen at **463 hosts**, collecting from a residential UK vantage.

17 hosts were excluded because this vantage cannot resolve them — 14 of them
advertising and tracking vendors blocked by DNS filtering on the local network.
That is a property of the measuring machine, not of those hosts, and it is **the
largest known limitation of the dataset**: ad technology is exactly the category
most likely to have an opinion about automated access, and it is the category
this vantage cannot see. Reasons are published per host in
[`panel/VANTAGE-EXCLUSIONS.md`](panel/VANTAGE-EXCLUSIONS.md) and in
`panel/excluded.csv`.

They are excluded rather than probed because a host the resolver refuses to look
up records as unreachable, which reads as a finding about the host. `run`
resolves every panel host before probing anything and refuses to start if any
fail, so this cannot quietly grow.

## Usage

Needs [uv](https://docs.astral.sh/uv/). No virtualenv, no lockfile — the single
pinned dependency lives in the script header.

```sh
uv run tests/test_robots.py          # parser: the correctness traps
uv run tests/test_fetch.py           # fetcher + conduct limits, local server only

uv run observatory.py screen panel/tranco-467YX-top1000.csv  # offline worksheet
# ... review every row in panel/screen.csv, then:
uv run observatory.py freeze                                 # offline
uv run observatory.py run --dry-run --limit 10                # eyeball it
uv run observatory.py run                                     # ~14 minutes
uv run observatory.py derive
```

Set `OBSERVATORY_CONTACT`, `OBSERVATORY_EMAIL` and `OBSERVATORY_VANTAGE` before
any real run.

## Layout

| Path | |
|---|---|
| `observatory.py` | collector: `screen`, `run`, `derive` |
| `robots_parse.py` | directive-preserving parser, `PARSER_VERSION` |
| `agents.txt` | AI user-agent watchlist (every token seen is recorded regardless) |
| `optout.yaml` | hosts excluded from every run |
| `panel/screen.csv` | the worksheet: one decision per Tranco row, with a reason |
| `panel/panel-01.csv` | the frozen cohort — append-only, never edited in place |
| `panel/excluded.csv` | what was screened out and why — the other half of the record |
| `data/robots/<host>.txt` | current robots.txt per host; `git log -p` is the change history |
| `docs/` | the published site; GitHub Pages serves this folder directly |
| `data/runs/<panel>/<date>/records/` | one `rank-LO-HI.jsonl` per rank band — fetch the band you care about |
| `data/runs/<panel>/<date>/run-meta.json` | provenance: panel hash, parser version, git SHA, degraded reasons |
| `data/blobs/` | archived response bodies, content-addressed — **local only, not committed** |

Records hold status, timings, every header and a `sha256` of each body; the
bodies live in `data/blobs/` and are not published. `reparse` reads them, so it
only works where the blob store is present.

## Reproducibility

Each run records the panel hash, parser version, git SHA, pinned `httpx`
version, Python version, vantage and opt-out list hash. The raw Tranco list is
committed as downloaded. The panel was selected by hand and **the decisions are
the record** — read `panel-01.csv` and `excluded.csv` and disagree with any
single row.

## Licence

Code: MIT. Data under `data/` and `panel/`: CC0. See
[LICENSE](LICENSE) and [LICENSE-DATA](LICENSE-DATA).

**To stop us crawling you**, put `User-agent: RobotsObservatory` /
`Disallow: /` in your robots.txt — the standard mechanism, honoured in
full. To have a host removed from the published dataset as well, [open an issue](https://github.com/florianhoehn/robots-observatory/issues/new). See [CONDUCT.md](CONDUCT.md).
