# Robots Observatory

How does the web *declare* its policy toward AI agents — and does that
declaration change depending on who asks?

A frozen panel of 480 real websites, drawn from the Tranco top 1000 and
screened down to hosts that are actually websites. Every week we read each
host's policy surface — `robots.txt` plus three well-known paths — under two
requester identities, and publish the result.

**Policy only.** This project reads the files the web publishes for machines to
read. It never fetches a page, so it measures what sites *say*, not what they
*enforce*. Those are different datasets; this is the first one. See
[CONDUCT.md](CONDUCT.md).

## Status

Panel frozen, no run yet. `panel-01` holds **480 hosts** screened from Tranco
list 467YX: 520 of the top 1000 were excluded as CDN and asset delivery, ad and
analytics serving endpoints, cloud and API endpoints, DNS and certificate
infrastructure, duplicates, parked domains and hosts with no public site.

Sector split: commerce 253, news 75, developer 62, reference 43, government 25,
other 22. The panel is more than half commerce, because the head of a
popularity list is dominated by platforms and retailers — any finding broken
down by sector should say so rather than let a commerce-heavy panel stand in
for the web.

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
| `data/runs/<date>/` | `records.jsonl` + `run-meta.json` |

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
