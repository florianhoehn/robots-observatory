#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx==0.28.1"]
# ///
"""Agent Access Observatory -- weekly panel collector.

Measures how a frozen panel of real websites *declares* its policy toward AI
agents. Policy surface only: robots.txt and three well-known paths. Nothing
here ever requests a page a host asked bots not to fetch -- these files exist
to be read by machines. See CONDUCT.md.

No model in the measurement path: fetch, parse, count, all deterministic.

    uv run observatory.py screen   panel/tranco-<id>-top1000.csv
    uv run observatory.py run      [--limit N] [--host H] [--dry-run]
    uv run observatory.py derive

The httpx pin is part of the dataset's provenance -- a change to its redirect
or header handling would alter results.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx

import robots_parse

ROOT = Path(__file__).parent
PANEL_DIR = ROOT / "panel"
DATA = ROOT / "data"
ROBOTS_DIR = DATA / "robots"
RUNS = DATA / "runs"
SITE_DATA = ROOT / "docs" / "data"

SCHEMA = 1
PANEL_ID = "panel-01"

# --- conduct limits. Changing any of these changes the ethical argument in
# --- CONDUCT.md, so they live here in one place and are recorded per run.
CONCURRENT_HOSTS = 6
HOST_GAP_S = 1.5
MAX_REDIRECTS = 3
READ_CAP_BYTES = 512 * 1024
TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)

# Injectable so the conduct limits below are verifiable against a local test
# server. Production runs never set it.
SCHEME = os.environ.get("OBSERVATORY_SCHEME", "https")

CONTACT = os.environ.get("OBSERVATORY_CONTACT", "https://example.invalid/bot")
CONTACT_EMAIL = os.environ.get("OBSERVATORY_EMAIL", "")

# Two identities. Both carry contact details, the browser control included --
# no probe this project makes is ever anonymous. The signed identity is
# deliberately absent: it only earns its keep once something can refuse us,
# which needs enforcement probing.
#
# `From:` is an email mailbox per RFC 9110, so it is set only when an address is
# actually configured; the UA's `(+url)` convention plus X-Observatory-Contact
# carry attribution either way. Shipping a URL in `From:` would be malformed.
_BASE_HEADERS = {"X-Observatory-Contact": CONTACT}
if "@" in CONTACT_EMAIL and "/" not in CONTACT_EMAIL:
    _BASE_HEADERS["From"] = CONTACT_EMAIL

IDENTITIES = {
    "honest_bot": {
        "User-Agent": f"RobotsObservatory/1.0 (+{CONTACT})",
        **_BASE_HEADERS,
    },
    "browser_control": {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
        ),
        **_BASE_HEADERS,
    },
}
US_TOKEN = "RobotsObservatory"

WELLKNOWN = {
    "llms_txt": "/llms.txt",
    "ai_txt": "/ai.txt",
    "tdmrep": "/.well-known/tdmrep.json",
}

# Stored response headers. An allowlist, so we never accidentally archive
# something identifying. `age` and `x-cache` matter because a CDN-cached
# robots.txt reads as "policy unchanged" when it may not be.
HEADER_ALLOWLIST = (
    "server", "content-type", "cache-control", "age", "x-cache", "via",
    "x-robots-tag", "cf-mitigated", "retry-after", "link",
    "tdm-reservation", "tdm-policy",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_watchlist() -> list[str]:
    path = ROOT / "agents.txt"
    if not path.exists():
        return []
    return [
        ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.startswith("#")
    ]


def load_optout() -> tuple[set[str], str]:
    """Hosts to skip, plus the list's hash for the run metadata.

    Read before every run, including the first, when it is empty -- the point of
    having it at run 1 is that the code path exists and is published.
    """
    path = ROOT / "optout.yaml"
    if not path.exists():
        return set(), hashlib.sha256(b"").hexdigest()
    raw = path.read_bytes()
    hosts = {
        ln.strip().lstrip("- ").strip().lower()
        for ln in raw.decode("utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    }
    return {h for h in hosts if h}, hashlib.sha256(raw).hexdigest()


def load_panel() -> list[dict]:
    path = PANEL_DIR / f"{PANEL_ID}.csv"
    if not path.exists():
        sys.exit(f"no panel at {path} -- run the screen step and freeze it first")
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def fetch(url: str, headers: dict) -> dict:
    """One polite GET. A fresh client per request means a fresh cookie jar;
    connection reuse would buy nothing against a 1.5s inter-request gap.

    Retries once on a transport error and never on an HTTP status -- a 403 is
    data, not a failure.
    """
    out: dict = {"status": None, "ms": None, "bytes": None, "sha256": None,
                 "truncated": False, "redirects": 0, "final_host": None,
                 "headers": {}, "error": None}
    body: str | None = None

    for attempt in (1, 2):
        t0 = time.monotonic()
        try:
            with httpx.Client(
                follow_redirects=True, max_redirects=MAX_REDIRECTS,
                timeout=TIMEOUT, headers=headers,
            ) as client:
                with client.stream("GET", url) as resp:
                    chunks, total, truncated = [], 0, False
                    for chunk in resp.iter_bytes():
                        total += len(chunk)
                        if total > READ_CAP_BYTES:
                            chunks.append(chunk[: READ_CAP_BYTES - (total - len(chunk))])
                            truncated = True
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)

                out["status"] = resp.status_code
                out["ms"] = int((time.monotonic() - t0) * 1000)
                out["bytes"] = len(raw)
                out["sha256"] = hashlib.sha256(raw).hexdigest()
                out["truncated"] = truncated
                out["redirects"] = len(resp.history)
                out["final_host"] = resp.url.host
                out["headers"] = {
                    k: resp.headers[k] for k in HEADER_ALLOWLIST if k in resp.headers
                }
                if "set-cookie" in resp.headers:
                    out["headers"]["set-cookie"] = "<present>"
                body = raw.decode("utf-8", errors="replace")
            break
        except httpx.HTTPError as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:200]
            if attempt == 2:
                break
            time.sleep(1.0)

    out["_body"] = body
    return out


def probe_host(row: dict, watchlist: list[str], run_id: str, vantage: str,
               write_text: bool) -> dict:
    """All five requests for one host, sequential with a pause between them."""
    host = row["host"].strip().lower()
    rec: dict = {
        "run_id": run_id, "panel_id": PANEL_ID, "schema": SCHEMA,
        "parser_version": robots_parse.PARSER_VERSION,
        "host": host,
        "rank_at_freeze": int(row["rank_at_freeze"]) if row.get("rank_at_freeze") else None,
        "sector": row.get("sector") or None,
        "adult": (row.get("adult") or "").strip().lower() in ("1", "true", "yes"),
        "vantage": vantage,
        "robots": [], "wellknown": {}, "errors": [],
    }

    bodies: dict[str, str | None] = {}
    first = True
    for ident, headers in IDENTITIES.items():
        if not first:
            time.sleep(HOST_GAP_S)
        first = False
        got = fetch(f"{SCHEME}://{host}/robots.txt", headers)
        body = got.pop("_body")
        bodies[ident] = body
        state = robots_parse.classify(got["status"], body, got["error"] is not None)
        entry = {"identity": ident, **got, "file_state": state,
                 "soft_404": state == "soft_404"}
        if got["error"]:
            rec["errors"].append(f"robots/{ident}: {got['error']}")
        rec["robots"].append(entry)

    hashes = {e["identity"]: e["sha256"] for e in rec["robots"] if e["sha256"]}
    rec["robots_varies_by_identity"] = len(set(hashes.values())) > 1

    # Parse the honest identity's copy -- that is the policy as declared to a
    # crawler that says what it is.
    honest = next(e for e in rec["robots"] if e["identity"] == "honest_bot")
    rec["policy"] = {"file_state": honest["file_state"]}
    if honest["file_state"] == "present" and bodies["honest_bot"]:
        rec["policy"].update(
            robots_parse.summarise(bodies["honest_bot"], watchlist, US_TOKEN)
        )
        if write_text:
            (ROBOTS_DIR / f"{host}.txt").write_text(
                bodies["honest_bot"], encoding="utf-8"
            )

    # Opt-out, honoured through the standard mechanism rather than a convention
    # of our own: a host that names our token and disallows it gets read once
    # (RFC 9309 exempts robots.txt itself from robots.txt rules) and nothing
    # further. Its declared policy still counts as a finding -- that policy is
    # public and is the thing being measured. Full removal from the published
    # dataset is the separate optout.yaml / email route, per CONDUCT.md.
    rec["opted_out_via_robots"] = bool(rec["policy"].get("would_disallow_us"))
    if rec["opted_out_via_robots"]:
        rec["wellknown"] = {k: {"status": None, "skipped": "robots_optout"}
                            for k in WELLKNOWN}
        return rec

    for key, path in WELLKNOWN.items():
        time.sleep(HOST_GAP_S)
        got = fetch(f"{SCHEME}://{host}{path}", IDENTITIES["honest_bot"])
        got.pop("_body")
        rec["wellknown"][key] = {
            k: got[k] for k in ("status", "bytes", "sha256", "error") if got[k] is not None
        } or {"status": None}
        if got["error"]:
            rec["errors"].append(f"{key}: {got['error']}")

    return rec


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_screen(args) -> None:
    """Liveness pass over a raw Tranco list.

    One request per apex, recording status and content-type. This is the cheap
    mechanical signal that kills most API and dead hosts before the hand pass;
    it does not decide anything on its own.
    """
    src = Path(args.listfile)
    rows = [r for r in csv.reader(src.open(encoding="utf-8")) if r]
    hosts = [(int(r[0]), r[1]) for r in rows if len(r) >= 2 and r[0].isdigit()]
    if args.limit:
        hosts = hosts[: args.limit]
    print(f"{len(hosts)} hosts from {src.name}", file=sys.stderr)

    lock = threading.Lock()
    out: list[dict] = []

    def one(item):
        rank, host = item
        got = fetch(f"{SCHEME}://{host}/", IDENTITIES["honest_bot"])
        body = got.pop("_body") or ""
        title = ""
        m = re.search(r"<title[^>]*>(.*?)</title>", body[:20000],
                      re.I | re.S)
        if m:
            title = re.sub(r"\s+", " ", m.group(1)).strip()[:80]
        row = {
            "rank": rank, "host": host, "status": got["status"],
            "content_type": got["headers"].get("content-type", ""),
            "final_host": got["final_host"] or "", "title": title,
            "error": got["error"] or "",
        }
        with lock:
            out.append(row)
            if len(out) % 50 == 0:
                print(f"  {len(out)}/{len(hosts)}", file=sys.stderr)

    with ThreadPoolExecutor(CONCURRENT_HOSTS) as pool:
        list(pool.map(one, hosts))

    out.sort(key=lambda r: r["host"])
    dest = PANEL_DIR / "screen.csv"
    with dest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {dest} -- sorted by host, ready for the hand pass", file=sys.stderr)
    print("  ccTLD duplicates sit together when sorted; keep the highest rank",
          file=sys.stderr)


def cmd_run(args) -> None:
    panel = load_panel()
    optout, optout_hash = load_optout()
    watchlist = load_watchlist()

    skipped = [r for r in panel if r["host"].strip().lower() in optout]
    panel = [r for r in panel if r["host"].strip().lower() not in optout]
    if skipped:
        print(f"opt-out: skipping {len(skipped)} host(s)", file=sys.stderr)

    if args.host:
        panel = [r for r in panel if r["host"] == args.host]
    if args.limit:
        panel = panel[: args.limit]

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vantage = os.environ.get("OBSERVATORY_VANTAGE", "local")
    run_dir = RUNS / ("dry-run" if args.dry_run else run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    records_path = run_dir / "records.jsonl"

    print(f"run {run_id} | {len(panel)} hosts | {len(panel) * 5} requests | "
          f"vantage {vantage}", file=sys.stderr)
    started, t0 = utc_now(), time.monotonic()
    ok = 0
    lock = threading.Lock()

    # Append as each host completes: less code than accumulating, and a run that
    # dies halfway leaves usable partial data. A failed run is 14 minutes to
    # redo, so there is no resume machinery.
    with records_path.open("w", encoding="utf-8") as fh:
        with ThreadPoolExecutor(CONCURRENT_HOSTS) as pool:
            futures = [
                pool.submit(probe_host, r, watchlist, run_id, vantage,
                            not args.dry_run)
                for r in panel
            ]
            for i, fut in enumerate(as_completed(futures), 1):
                rec = fut.result()
                with lock:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
                    fh.flush()
                    if not rec["errors"]:
                        ok += 1
                    if i % 25 == 0 or i == len(futures):
                        print(f"  {i}/{len(futures)}  ok={ok}", file=sys.stderr)

    attempted = len(panel)
    err_rate = 0.0 if not attempted else (attempted - ok) / attempted
    meta = {
        "run_id": run_id, "started": started,
        "duration_s": round(time.monotonic() - t0, 1),
        "panel_id": PANEL_ID, "hosts_attempted": attempted, "hosts_ok": ok,
        # A degraded run is marked and not derived from, rather than quietly
        # published as a bad week in the time series.
        "status": "degraded" if err_rate > 0.05 else "complete",
        "error_rate": round(err_rate, 4),
        "panel_sha256": hashlib.sha256(
            (PANEL_DIR / f"{PANEL_ID}.csv").read_bytes()
        ).hexdigest(),
        "parser_version": robots_parse.PARSER_VERSION,
        "code_git_sha": git_sha(),
        "httpx_version": httpx.__version__,
        "python": sys.version.split()[0],
        "vantage": vantage,
        "identities": sorted(IDENTITIES),
        "wellknown_paths": WELLKNOWN,
        "conduct": {"concurrent_hosts": CONCURRENT_HOSTS,
                    "host_gap_s": HOST_GAP_S,
                    "read_cap_bytes": READ_CAP_BYTES},
        "optout_sha256": optout_hash, "optout_count": len(skipped),
        "watchlist_size": len(watchlist),
    }
    (run_dir / "run-meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\n{meta['status']}: {ok}/{attempted} clean, "
          f"{meta['duration_s']}s -> {records_path}", file=sys.stderr)
    if meta["status"] == "degraded":
        sys.exit("error rate above 5% -- investigate before deriving")


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def cmd_derive(args) -> None:
    runs = sorted(d for d in RUNS.iterdir()
                  if d.is_dir() and (d / "records.jsonl").exists()
                  and d.name != "dry-run")
    if not runs:
        sys.exit("no runs to derive from")
    run_dir = runs[-1]
    recs = [json.loads(ln) for ln in
            (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    n = len(recs)

    def share(pred) -> dict:
        c = sum(1 for r in recs if pred(r))
        return {"n": c, "pct": round(100 * c / n, 1) if n else 0.0}

    def pol(r, key, default=None):
        return r.get("policy", {}).get(key, default)

    tokens: dict[str, dict[str, int]] = {}
    for r in recs:
        for t in pol(r, "ai_tokens_named", []) or []:
            e = tokens.setdefault(t, {"named": 0, "disallowed": 0})
            e["named"] += 1
            if t in (pol(r, "ai_tokens_root_disallowed", []) or []):
                e["disallowed"] += 1

    states: dict[str, int] = {}
    for r in recs:
        states[pol(r, "file_state", "error")] = states.get(pol(r, "file_state", "error"), 0) + 1

    sectors: dict[str, dict] = {}
    for r in recs:
        s = r.get("sector") or "unlabelled"
        e = sectors.setdefault(s, {"hosts": 0, "names_ai": 0, "wildcard_block": 0})
        e["hosts"] += 1
        if pol(r, "ai_tokens_named"):
            e["names_ai"] += 1
        if pol(r, "wildcard_root_disallowed"):
            e["wildcard_block"] += 1

    findings = {
        "run_id": recs[0]["run_id"], "panel_id": PANEL_ID, "hosts": n,
        "generated": utc_now(),
        "robots_file_state": states,
        "names_any_ai_agent": share(lambda r: bool(pol(r, "ai_tokens_named"))),
        "has_robots_but_silent_on_ai": share(
            lambda r: pol(r, "file_state") == "present" and not pol(r, "ai_tokens_named")
        ),
        "no_robots_at_all": share(lambda r: pol(r, "file_state") == "absent_404"),
        "wildcard_root_disallowed": share(
            lambda r: bool(pol(r, "wildcard_root_disallowed"))
        ),
        "would_disallow_us": share(lambda r: bool(pol(r, "would_disallow_us"))),
        "agent_tokens": dict(sorted(
            tokens.items(), key=lambda kv: -kv[1]["named"]
        )[:20]),
        "wellknown": {
            k: share(lambda r, k=k: r.get("wellknown", {}).get(k, {}).get("status") == 200)
            for k in WELLKNOWN
        },
        "rsl_license_refs": share(lambda r: bool(pol(r, "rsl_license_refs"))),
        "robots_varies_by_identity": share(
            lambda r: bool(r.get("robots_varies_by_identity"))
        ),
        "soft_404": share(lambda r: pol(r, "file_state") == "soft_404"),
        "opted_out_via_robots": share(
            lambda r: bool(r.get("opted_out_via_robots"))
        ),
        "by_sector": sectors,
    }

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    (SITE_DATA / "findings.json").write_text(
        json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    flat = DATA / "hosts.csv"
    cols = ["run_id", "host", "rank_at_freeze", "sector", "adult", "file_state",
            "names_ai", "ai_tokens_named", "ai_tokens_root_disallowed",
            "wildcard_root_disallowed", "would_disallow_us", "rsl",
            "llms_txt", "ai_txt", "tdmrep", "varies_by_identity"]
    with flat.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in sorted(recs, key=lambda r: r["host"]):
            wk = r.get("wellknown", {})
            w.writerow([
                r["run_id"], r["host"], r.get("rank_at_freeze"), r.get("sector"),
                r.get("adult"), pol(r, "file_state"),
                bool(pol(r, "ai_tokens_named")),
                "|".join(pol(r, "ai_tokens_named", []) or []),
                "|".join(pol(r, "ai_tokens_root_disallowed", []) or []),
                pol(r, "wildcard_root_disallowed"), pol(r, "would_disallow_us"),
                "|".join(pol(r, "rsl_license_refs", []) or []),
                wk.get("llms_txt", {}).get("status"),
                wk.get("ai_txt", {}).get("status"),
                wk.get("tdmrep", {}).get("status"),
                r.get("robots_varies_by_identity"),
            ])

    print(json.dumps(findings, indent=2, sort_keys=True))
    print(f"\nwrote {SITE_DATA / 'findings.json'} and {flat}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen", help="liveness pass over a raw Tranco list")
    s.add_argument("listfile")
    s.add_argument("--limit", type=int)
    s.set_defaults(func=cmd_screen)

    r = sub.add_parser("run", help="probe the frozen panel")
    r.add_argument("--limit", type=int)
    r.add_argument("--host")
    r.add_argument("--run-id")
    r.add_argument("--dry-run", action="store_true",
                   help="write to data/runs/dry-run and leave robots texts alone")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("derive", help="compute findings from the latest run")
    d.set_defaults(func=cmd_derive)

    args = ap.parse_args()
    for p in (ROBOTS_DIR, RUNS, SITE_DATA):
        p.mkdir(parents=True, exist_ok=True)
    random.seed(0)
    args.func(args)


if __name__ == "__main__":
    main()
