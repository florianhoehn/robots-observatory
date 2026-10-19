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

    uv run observatory.py screen   panel/tranco-<id>-top1000.csv   # offline
    uv run observatory.py freeze                                   # offline
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
ERROR_BUDGET = 0.05
HOST_GAP_S = 1.5
MAX_REDIRECTS = 5
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


# Canaries for the resolver check below. The controls are ordinary websites;
# the filtered set is the archetypal ad and tracking domains that network-level
# blockers remove. None of the filtered set is in the panel -- they are here
# purely to detect a resolver that will not look such hosts up.
RESOLVER_CONTROLS = ("google.com", "bbc.com", "wikipedia.org")
RESOLVER_CANARIES = ("doubleclick.net", "google-analytics.com",
                     "criteo.com", "taboola.com", "scorecardresearch.com")


def resolver_check() -> dict:
    """Detect a filtering resolver before probing anything.

    A vantage with DNS-level ad blocking silently deletes an entire category of
    host from the dataset, and the records look like the hosts are unreachable
    rather than like the measuring machine refused to ask. Run 1 from a
    residential machine hit exactly this: twelve ad-technology vendors in the
    panel were unresolvable while ordinary websites resolved fine.
    """
    import socket

    def resolves(h: str) -> bool:
        try:
            socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP)
            return True
        except OSError:
            return False

    controls = {h: resolves(h) for h in RESOLVER_CONTROLS}
    canaries = {h: resolves(h) for h in RESOLVER_CANARIES}
    controls_ok = all(controls.values())
    blocked = [h for h, ok in canaries.items() if not ok]
    return {
        "controls": controls, "canaries": canaries,
        "controls_resolved": controls_ok,
        "canaries_blocked": len(blocked),
        # Controls fine but canaries blocked is the signature of a filtering
        # resolver; controls failing is just no network.
        "filtering_detected": controls_ok and len(blocked) >= 2,
    }


def resolve_panel(panel: list[dict]) -> dict[str, str]:
    """Resolve every panel host. Returns host -> error for the failures.

    The run calls this before probing anything. A host the resolver will not
    look up records as unreachable, and that reads as a finding about the host
    rather than about the measuring machine -- so the run stops rather than
    collect it.
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def one(host: str):
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            return host, None
        except OSError as exc:
            return host, f"{exc.errno}:{exc.strerror}"

    with ThreadPoolExecutor(16) as pool:
        return {h: e for h, e in pool.map(one, [r["host"] for r in panel]) if e}


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
                 "final_path": None, "path_mismatch": False,
                 "headers": {}, "error": None, "dns_error": False}
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
                out["final_path"] = resp.url.path
                # Landing at a different path means this is not the file we
                # asked for -- a consent wall or a homepage, not a 404.
                out["path_mismatch"] = (
                    resp.url.path.rstrip("/") != httpx.URL(url).path.rstrip("/"))
                out["headers"] = {
                    k: resp.headers[k] for k in HEADER_ALLOWLIST if k in resp.headers
                }
                if "set-cookie" in resp.headers:
                    out["headers"]["set-cookie"] = "<present>"
                body = raw.decode("utf-8", errors="replace")
            break
        except httpx.HTTPError as exc:
            out["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out["dns_error"] = robots_parse.is_dns_error(out["error"])
            if out["dns_error"] or attempt == 2:
                break  # retrying a name that does not resolve is pointless
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
        state = robots_parse.classify(got["status"], body,
                                      got["error"] is not None,
                                      got["path_mismatch"], got["dns_error"])
        entry = {"identity": ident, **got, "file_state": state}
        if got["error"]:
            rec["errors"].append(f"robots/{ident}: {got['error']}")
        rec["robots"].append(entry)

    # Three separate questions, which a single hash comparison conflates. The
    # raw comparison said 11% of the panel varies its policy by requester; of
    # those, 94% were two error pages or two consent pages whose bytes differ
    # because of an embedded nonce. Reported as one number it is simply wrong.
    hashes = {e["identity"]: e["sha256"] for e in rec["robots"] if e["sha256"]}
    n_ident = len(IDENTITIES)
    rec["policy_varies_by_identity"] = robots_parse.policy_varies(
        rec["robots"], n_ident)
    rec["access_varies_by_identity"] = robots_parse.access_varies(
        rec["robots"], n_ident)
    # Diagnostic only. Includes cosmetic differences; never a finding.
    rec["hash_differs_by_identity"] = len(set(hashes.values())) > 1

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
    named_us = US_TOKEN.lower() in {
        t.lower() for t in (rec["policy"].get("ai_tokens_named") or [])
    } or US_TOKEN.lower() in {
        t.lower() for t in (rec["policy"].get("all_agent_tokens_seen") or [])
    }
    blocks_us = bool(rec["policy"].get("would_disallow_us"))
    rec["opted_out_via_robots"] = blocks_us and named_us
    rec["wildcard_blocks_us"] = blocks_us and not named_us
    if blocks_us:
        why = "named_our_token" if named_us else "wildcard_disallow_all"
        rec["wellknown"] = {k: {"status": None, "file_state": "skipped",
                                "skipped": why} for k in WELLKNOWN}
        return rec

    for key, path in WELLKNOWN.items():
        time.sleep(HOST_GAP_S)
        got = fetch(f"{SCHEME}://{host}{path}", IDENTITIES["honest_bot"])
        body = got.pop("_body")
        state = robots_parse.classify(got["status"], body,
                                      got["error"] is not None,
                                      got["path_mismatch"], got["dns_error"])
        rec["wellknown"][key] = {
            "status": got["status"], "file_state": state,
            "bytes": got["bytes"], "sha256": got["sha256"],
            "redirects": got["redirects"], "final_path": got["final_path"],
        }
        if got["error"]:
            rec["errors"].append(f"{key}: {got['error']}")

    return rec


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_screen(args) -> None:
    """Turn a raw Tranco list into a worksheet for the hand pass.

    Entirely offline. An earlier draft fetched each homepage for a liveness
    signal, which would have made CONDUCT.md's "never requests a page" false for
    the sake of a column a human does not need -- nobody has to fetch
    doubleclick.net to know it is not a website. Hosts that turn out to be dead
    simply record an error in run 1, which is better data than silently
    dropping them beforehand.

    Rows are grouped by the label before the first dot and then by rank, so
    every google.* or amazon.* sits together with the highest-ranked first:
    keep that one, exclude the rest as you pass. That is the ccTLD collapse,
    done by eye, with no public-suffix dependency.
    """
    src = Path(args.listfile)
    rows = [r for r in csv.reader(src.open(encoding="utf-8")) if r]
    hosts = [(int(r[0]), r[1].strip().lower())
             for r in rows if len(r) >= 2 and r[0].isdigit()]
    if args.limit:
        hosts = hosts[: args.limit]

    groups: dict[str, int] = {}
    for _, host in hosts:
        label = host.split(".")[0]
        groups[label] = groups.get(label, 0) + 1

    out = [
        {"rank": rank, "host": host,
         "group": host.split(".")[0],
         "group_n": groups[host.split(".")[0]],
         "decision": "", "sector": "", "adult": "", "reason": ""}
        for rank, host in hosts
    ]
    out.sort(key=lambda r: (r["group"], r["rank"]))

    dest = PANEL_DIR / "screen.csv"
    with dest.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0]))
        w.writeheader()
        w.writerows(out)

    dupes = sum(1 for r in out if r["group_n"] > 1)
    print(f"{len(out)} hosts -> {dest}", file=sys.stderr)
    print(f"  {dupes} rows sit in a multi-host group (the ccTLD collapse "
          f"candidates); {len(out) - dupes} are singletons", file=sys.stderr)
    print("  fill `decision` with keep or exclude, plus sector for keeps "
          "and reason for excludes", file=sys.stderr)
    print("  sectors: news commerce government reference developer other",
          file=sys.stderr)
    print("\n  No network requests were made. This project never fetches a page.",
          file=sys.stderr)


def cmd_freeze(args) -> None:
    """Split a completed worksheet into the frozen panel and the exclusion record.

    Both halves are published: the panel is what every future run is measured
    against, and excluded.csv is the other half of the record, so a reader can
    disagree with any single row rather than audit a rule engine.
    """
    src = PANEL_DIR / "screen.csv"
    rows = list(csv.DictReader(src.open(encoding="utf-8")))
    SECTORS = {"news", "commerce", "government", "reference", "developer", "other"}

    keep, drop, bad = [], [], []
    for r in rows:
        d = (r["decision"] or "").strip().lower()
        if d == "keep":
            sec = (r["sector"] or "").strip().lower()
            if sec not in SECTORS:
                bad.append(f"{r['host']}: sector {sec!r} not in {sorted(SECTORS)}")
            keep.append(r)
        elif d == "exclude":
            if not (r["reason"] or "").strip():
                bad.append(f"{r['host']}: excluded with no reason")
            drop.append(r)
        else:
            bad.append(f"{r['host']}: decision {d!r} is neither keep nor exclude")

    if bad:
        for b in bad[:25]:
            print(f"  {b}", file=sys.stderr)
        if len(bad) > 25:
            print(f"  ... and {len(bad) - 25} more", file=sys.stderr)
        sys.exit(f"{len(bad)} row(s) incomplete -- every row must be decided")

    panel = PANEL_DIR / f"{PANEL_ID}.csv"
    if panel.exists() and not args.force:
        sys.exit(f"{panel} already exists. The panel is append-only history -- "
                 f"a refresh mints panel-02 rather than editing this. Use "
                 f"--force only if run 1 has not happened.")

    with panel.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["host", "rank_at_freeze", "sector", "adult"])
        for r in sorted(keep, key=lambda r: int(r["rank"])):
            w.writerow([r["host"], r["rank"], r["sector"].strip().lower(),
                        "true" if (r["adult"] or "").strip().lower() in
                        ("1", "true", "yes", "y") else "false"])

    with (PANEL_DIR / "excluded.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["host", "rank", "reason"])
        for r in sorted(drop, key=lambda r: int(r["rank"])):
            w.writerow([r["host"], r["rank"], r["reason"].strip()])

    # Conservation: every input row lands in exactly one output file.
    assert len(keep) + len(drop) == len(rows), "rows lost between the two outputs"
    sectors: dict[str, int] = {}
    for r in keep:
        k = r["sector"].strip().lower()
        sectors[k] = sectors.get(k, 0) + 1
    print(f"{len(rows)} screened -> {len(keep)} in {PANEL_ID}, "
          f"{len(drop)} excluded", file=sys.stderr)
    print(f"  sectors: {dict(sorted(sectors.items()))}", file=sys.stderr)
    print(f"  panel sha256: "
          f"{hashlib.sha256(panel.read_bytes()).hexdigest()[:16]}...", file=sys.stderr)


def retry_failed(run_dir: Path, panel: list[dict], watchlist: list[str],
                 run_id: str, vantage: str, write_text: bool,
                 pause_s: float = 30.0) -> dict:
    """Re-probe hosts whose first pass errored, once, after a pause.

    A transient timeout recorded as a policy fact is a wrong answer, so a host
    that fails gets one more attempt before its failure is treated as data.
    Hosts that fail twice are genuinely unreachable from this vantage, which is
    itself a finding -- a TLS handshake refused from a UK residential IP is a
    fact about reachability, not a measurement error.
    """
    recs_path = run_dir / "records.jsonl"
    recs = [json.loads(ln) for ln in
            recs_path.read_text(encoding="utf-8").splitlines() if ln]
    by_host = {r["host"]: r for r in recs}
    failed = sorted(h for h, r in by_host.items() if r["errors"])
    if not failed:
        return {"attempted": 0, "recovered": 0, "still_failing": []}

    print(f"retry pass: {len(failed)} host(s), pausing {pause_s:.0f}s first",
          file=sys.stderr)
    time.sleep(pause_s)
    rows = {r["host"]: r for r in panel}
    recovered = []
    with ThreadPoolExecutor(CONCURRENT_HOSTS) as pool:
        futs = {pool.submit(probe_host, rows[h], watchlist, run_id, vantage,
                            write_text): h for h in failed if h in rows}
        for fut in as_completed(futs):
            rec = fut.result()
            rec["retried"] = True
            if not rec["errors"]:
                recovered.append(rec["host"])
            by_host[rec["host"]] = rec

    with recs_path.open("w", encoding="utf-8") as fh:
        for h in sorted(by_host):
            fh.write(json.dumps(by_host[h], sort_keys=True) + "\n")
    still = sorted(h for h, r in by_host.items() if r["errors"])
    print(f"  recovered {len(recovered)} of {len(failed)}; "
          f"{len(still)} still failing", file=sys.stderr)
    return {"attempted": len(failed), "recovered": sorted(recovered),
            "still_failing": still}


def cmd_retry(args) -> None:
    """Run the retry pass against the latest run and rewrite its metadata."""
    runs = sorted(d for d in RUNS.iterdir()
                  if d.is_dir() and (d / "run-meta.json").exists()
                  and d.name != "dry-run")
    if not runs:
        sys.exit("no run to retry")
    run_dir = runs[-1]
    meta = json.loads((run_dir / "run-meta.json").read_text())
    panel = load_panel()
    res = retry_failed(run_dir, panel, load_watchlist(), meta["run_id"],
                       meta["vantage"], True, pause_s=args.pause)

    recs = [json.loads(ln) for ln in
            (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines() if ln]
    ok = sum(1 for r in recs if not r["errors"])
    err_rate = (len(recs) - ok) / len(recs) if recs else 0.0
    reasons = [f"error_rate_{err_rate:.3f}"] if err_rate > ERROR_BUDGET else []
    meta.update(hosts_ok=ok, error_rate=round(err_rate, 4),
                retry_pass=res,
                status="degraded" if reasons else "complete",
                degraded_reasons=reasons)
    (run_dir / "run-meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\n{meta['status']}: {ok}/{len(recs)} clean "
          f"({err_rate:.1%} error rate)", file=sys.stderr)


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

    resolver = resolver_check()
    if not resolver["controls_resolved"]:
        sys.exit("resolver cannot reach ordinary websites -- check the network")

    # The gate is the panel, not the canaries. A filtering resolver only
    # corrupts the data if it filters something the panel actually contains;
    # the canary result is recorded either way so a reader can see what this
    # vantage does.
    unresolvable = resolve_panel(panel)
    if unresolvable and not args.allow_unresolvable:
        lines = "\n".join(f"    {h:<26}{e}" for h, e in sorted(unresolvable.items()))
        sys.exit(
            f"{len(unresolvable)} PANEL HOST(S) DO NOT RESOLVE from this vantage\n\n"
            f"{lines}\n\n"
            "  Probing these would record them as unreachable, which reads as a\n"
            "  finding about those hosts rather than about this machine.\n\n"
            "  Either exclude them from the panel with a documented reason (see\n"
            "  panel/VANTAGE-EXCLUSIONS.md) and re-freeze, run from a vantage\n"
            "  that resolves them, or pass --allow-unresolvable to collect them\n"
            "  anyway with the run marked degraded."
        )

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
    reasons = []
    if err_rate > ERROR_BUDGET:
        reasons.append(f"error_rate_{err_rate:.3f}")
    if unresolvable:
        reasons.append(f"unresolvable_panel_hosts_{len(unresolvable)}")
    meta = {
        "run_id": run_id, "started": started,
        "duration_s": round(time.monotonic() - t0, 1),
        "panel_id": PANEL_ID, "hosts_attempted": attempted, "hosts_ok": ok,
        # A degraded run is marked and not derived from, rather than quietly
        # published as a bad week in the time series.
        "status": "degraded" if reasons else "complete",
        "degraded_reasons": reasons,
        "error_rate": round(err_rate, 4),
        "resolver_check": resolver,
        "unresolvable_panel_hosts": sorted(unresolvable),
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
        sys.exit(f"degraded ({', '.join(reasons)}) -- investigate before deriving")


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            capture_output=True, text=True, timeout=5,
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def cmd_resolve(args) -> None:
    """Resolve every panel host and report the failures. Touches no host."""
    panel = load_panel()
    res = resolver_check()
    bad = resolve_panel(panel)
    print(f"panel: {len(panel)} hosts, {len(bad)} unresolvable", file=sys.stderr)
    for h, e in sorted(bad.items()):
        print(f"  {h:<26}{e}", file=sys.stderr)
    blocked = [h for h, ok in res["canaries"].items() if not ok]
    if blocked:
        print(f"\nthis vantage does not resolve {len(blocked)} of "
              f"{len(RESOLVER_CANARIES)} ad/tracker canaries: "
              f"{', '.join(blocked)}", file=sys.stderr)
        print("  recorded in every run's metadata as a property of the vantage",
              file=sys.stderr)
    if bad:
        sys.exit(1)


def _policy_varies(r: dict) -> bool:
    return robots_parse.policy_varies(r.get("robots", []), len(IDENTITIES))


def _access_varies(r: dict) -> bool:
    return robots_parse.access_varies(r.get("robots", []), len(IDENTITIES))


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
            k: share(lambda r, k=k:
                     r.get("wellknown", {}).get(k, {}).get("file_state") == "present")
            for k in WELLKNOWN
        },
        "rsl_license_refs": share(lambda r: bool(pol(r, "rsl_license_refs"))),
        # The headline comparison, defined narrowly on purpose: both
        # identities received a real policy file and the files differ.
        "policy_varies_by_identity": share(_policy_varies),
        # One identity got the file and the other did not. Differential
        # access, not differential policy -- a separate finding.
        "access_varies_by_identity": share(_access_varies),
        # Raw hash difference. Diagnostic only: dominated by error and consent
        # pages carrying nonces. Not a finding, kept so the gap is visible.
        "hash_differs_by_identity": share(
            lambda r: len({e["sha256"] for e in r.get("robots", [])
                           if e.get("sha256")}) > 1),
        "soft_404": share(lambda r: pol(r, "file_state") == "soft_404"),
        "dns_failure": share(lambda r: pol(r, "file_state") == "dns_failure"),
        "opted_out_via_robots": share(
            lambda r: bool(r.get("opted_out_via_robots"))
        ),
        "wildcard_blocks_us": share(lambda r: bool(r.get("wildcard_blocks_us"))),
        "challenged": share(lambda r: pol(r, "file_state") == "challenged"),
        "redirected_away": share(lambda r: pol(r, "file_state") == "redirected_away"),
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
                wk.get("llms_txt", {}).get("file_state"),
                wk.get("ai_txt", {}).get("file_state"),
                wk.get("tdmrep", {}).get("file_state"),
                r.get("robots_varies_by_identity"),
            ])

    print(json.dumps(findings, indent=2, sort_keys=True))
    print(f"\nwrote {SITE_DATA / 'findings.json'} and {flat}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screen", help="raw Tranco list -> worksheet (offline)")
    s.add_argument("listfile")
    s.add_argument("--limit", type=int)
    s.set_defaults(func=cmd_screen)

    z = sub.add_parser("freeze", help="completed worksheet -> frozen panel")
    z.add_argument("--force", action="store_true",
                   help="overwrite an existing panel; only before run 1")
    z.set_defaults(func=cmd_freeze)

    r = sub.add_parser("run", help="probe the frozen panel")
    r.add_argument("--limit", type=int)
    r.add_argument("--host")
    r.add_argument("--run-id")
    r.add_argument("--allow-unresolvable", action="store_true",
                   help="probe panel hosts that do not resolve; marks the run degraded")
    r.add_argument("--dry-run", action="store_true",
                   help="write to data/runs/dry-run and leave robots texts alone")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("resolve", help="check every panel host resolves (offline of the hosts)")
    v.set_defaults(func=cmd_resolve)

    t = sub.add_parser("retry", help="re-probe failed hosts in the latest run")
    t.add_argument("--pause", type=float, default=30.0)
    t.set_defaults(func=cmd_retry)

    d = sub.add_parser("derive", help="compute findings from the latest run")
    d.set_defaults(func=cmd_derive)

    args = ap.parse_args()
    for p in (ROBOTS_DIR, RUNS, SITE_DATA):
        p.mkdir(parents=True, exist_ok=True)
    random.seed(0)
    args.func(args)


if __name__ == "__main__":
    main()
