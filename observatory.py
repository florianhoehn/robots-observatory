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
BLOBS = DATA / "blobs"
RUNS = DATA / "runs"  # <panel>/<run_id>/ below this
SITE_DATA = ROOT / "docs" / "data"

SCHEMA = 1
# Set per invocation from --panel. panel-01 is the hand-screened top-1000
# cohort that carries trend claims; panel-02 is the unscreened 20k breadth
# survey. Records always name the panel they came from.
PANEL_ID = "panel-01"

# --- conduct limits. Changing any of these changes the ethical argument in
# --- CONDUCT.md, so they live here in one place and are recorded per run.
# 30, not 10. Measured throughput was 38% of theoretical because a host that
# times out on every path holds a worker for minutes, and ~9% of a 16k panel
# does something like that. This governs how many DIFFERENT hosts are read at
# once and never how fast any one of them is asked -- the 1.5s per-host gap is
# the politeness guarantee and is untouched. At 10 the run needed 21 hours; at
# 30 it needs about 7.
CONCURRENT_HOSTS = 30
ERROR_BUDGET = 0.05
# Share of a panel that may be unresolvable at run start before the run is
# refused. The guard exists to catch SYSTEMATIC loss -- a filtering resolver
# removing a whole category -- which showed up at 2.9% of a 480-host panel. A
# handful of transient DNS failures is normal on a 16,000-host panel and is
# honestly recorded as dns_failure, so aborting over 0.2% turns a 26-host
# footnote into a zero-host night.
UNRESOLVABLE_BUDGET = 0.01
HOST_GAP_S = 1.5
MAX_REDIRECTS = 5
READ_CAP_BYTES = 512 * 1024
# A real policy file is archived whole; a challenge or error page only needs
# enough to identify what it was.
ARCHIVE_CAP_PRESENT = 256 * 1024
ARCHIVE_CAP_OTHER = 32 * 1024
# A static policy file that has not answered in six seconds is not going to.
# These were 10/15 and, doubled by the retry and multiplied by twelve paths,
# cost a worker ten minutes per dead host -- which is what actually limited
# throughput, not the worker count.
TIMEOUT = httpx.Timeout(connect=5.0, read=6.0, write=5.0, pool=5.0)

# Hard ceiling on one host's whole probe. Past it the remaining paths are
# recorded as skipped rather than silently absent: a host that cannot answer
# in two minutes has told us what we needed to know, and the tail of slow
# hosts should not set the pace for the other sixteen thousand.
HOST_BUDGET_S = 120.0

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

# What each crawler is for, and who runs it, as its operator documents it.
# A judgement, so it is published rather than buried: a reader who disagrees
# with a classification can see it and re-cut the data.
AI_PURPOSE = {
    "GPTBot": "train", "Google-Extended": "train", "CCBot": "train",
    "Bytespider": "train", "anthropic-ai": "train", "ClaudeBot": "train",
    "cohere-ai": "train", "Diffbot": "train", "omgili": "train",
    "omgilibot": "train", "Applebot-Extended": "train", "Timpibot": "train",
    "meta-externalagent": "train", "ImagesiftBot": "train", "PetalBot": "train",
    "Amazonbot": "train", "AI2Bot": "train", "Webzio-Extended": "train",
    "SemrushBot-OCOB": "train",
    "OAI-SearchBot": "search", "PerplexityBot": "search", "YouBot": "search",
    "Applebot": "search", "FacebookBot": "search", "Claude-SearchBot": "search",
    "DuckAssistBot": "search",
    "ChatGPT-User": "user", "Perplexity-User": "user", "Claude-User": "user",
    "MistralAI-User": "user", "meta-externalfetcher": "user",
}
AI_OPERATOR = {
    "GPTBot": "OpenAI", "OAI-SearchBot": "OpenAI", "ChatGPT-User": "OpenAI",
    "ClaudeBot": "Anthropic", "anthropic-ai": "Anthropic",
    "Claude-User": "Anthropic", "Claude-SearchBot": "Anthropic",
    "PerplexityBot": "Perplexity", "Perplexity-User": "Perplexity",
    "Applebot": "Apple", "Applebot-Extended": "Apple",
    "meta-externalagent": "Meta", "FacebookBot": "Meta",
    "meta-externalfetcher": "Meta",
    "omgili": "Webz.io", "omgilibot": "Webz.io", "Webzio-Extended": "Webz.io",
}

# Paths the convention defines as JSON. A 200 at one of these carrying
# anything else is not that file -- see robots_parse.looks_like_json.
JSON_WELLKNOWN = frozenset({"tdmrep", "gpc", "agent_card", "ai_catalog",
                            "agent_card_legacy"})

WELLKNOWN = {
    "llms_txt": "/llms.txt",
    "ai_txt": "/ai.txt",
    "tdmrep": "/.well-known/tdmrep.json",
    # RFC 9116. Not an AI signal -- a control. It says whether an organisation
    # publishes machine-readable metadata at all, which is the baseline the
    # AI-specific files should be read against: a site with no security.txt
    # and no llms.txt has not declined to document itself for machines, it has
    # simply never done that for anyone.
    "security_txt": "/.well-known/security.txt",
    # Benchmark, not an AI signal. ads.txt is a machine-readable policy file
    # with real money behind it that went from nothing to mass adoption in
    # about two years. It gives llms.txt's number a scale to be read against:
    # 20% of what, compared to what?
    "ads_txt": "/ads.txt",
    # Global Privacy Control. A machine-readable rights signal that is legally
    # operative in several US states -- the closest existing thing to the
    # "declare your terms to automated readers" idea, arrived at from privacy
    # rather than from AI.
    "gpc": "/.well-known/gpc.json",
    # The human counterpart to robots.txt. Pure control: does this site publish
    # voluntary metadata files at all? A site with neither humans.txt nor
    # llms.txt has not declined to document itself for machines; it has never
    # documented itself for anyone.
    "humans_txt": "/humans.txt",
    # A2A Agent Card. The canonical path per the A2A spec -- note the hyphen:
    # v1.0 renamed it from the pre-1.0 /.well-known/agent.json, so a probe of
    # the old path would measure a convention that moved rather than one that
    # exists. Checked against a2a-protocol.org, not from memory.
    "agent_card": "/.well-known/agent-card.json",
    # AI Catalog: the domain-level discovery mechanism that both A2A and MCP
    # point at for "what AI artifacts does this domain publish". A working
    # draft under the Linux Foundation, pending an adoption vote.
    #
    # NOT /.well-known/mcp.json, which does not exist. MCP Server Cards
    # (SEP-2127, Final) live at <streamable-http-url>/server-card -- relative
    # to an MCP endpoint, so not discoverable from a domain apex and not
    # probeable by a crawler at all. Domain-level MCP discovery goes through
    # this catalog instead.
    "ai_catalog": "/.well-known/ai-catalog.json",
    # The pre-1.0 A2A path, probed alongside the current one so that a zero is
    # interpretable. "Nobody publishes an agent card" and "nobody publishes one
    # at the path the spec now names" are different claims, and only the pair
    # can tell them apart. It also measures something nobody tracks: how long a
    # renamed well-known path survives in the wild.
    "agent_card_legacy": "/.well-known/agent.json",
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


def blob_path(sha: str) -> Path:
    return BLOBS / sha[:2] / sha


def store_blob(sha: str | None, text: str | None, present: bool = True) -> bool:
    """Archive a response body once, addressed by its hash.

    The goal is that any future question can be answered from stored responses
    rather than by crawling again: a new parser, a new classifier, a question
    nobody thought to ask tonight. So EVERY body is kept, not only the ones
    that parsed -- otherwise "which vendors serve the challenge pages?" is
    permanently unanswerable.

    Content addressing makes that nearly free. Challenge pages and 404s are
    near-identical across thousands of hosts and collapse to a handful of
    blobs, and a policy file that never changes costs nothing on later runs.

    The one thing no archive can recover is a request never made. Adding a
    path to WELLKNOWN only ever applies going forward.

    The returned flag says whether what was written is a *prefix* of the body
    rather than the body. That distinction has to be recorded: the blob is
    named for the sha256 of the whole response, so a truncated one does not
    hash to its own filename and an integrity check would call it corrupt.
    39% of the archive is in that state, and nothing said so until now.
    """
    if not sha or text is None:
        return False
    cap = ARCHIVE_CAP_PRESENT if present else ARCHIVE_CAP_OTHER
    truncated = len(text) > cap
    if truncated:
        text = text[:cap]
    p = blob_path(sha)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return truncated


def read_blob(sha: str) -> str | None:
    p = blob_path(sha)
    return p.read_text(encoding="utf-8") if p.exists() else None


# ---------------------------------------------------------------- records ----
# A run's records are split across one file per rank band rather than written
# to a single records.jsonl. At panel-02 size that file reached 83 MB, which
# GitHub warns about and which forces anyone asking a narrow question ("what do
# the top 1000 publish?") to download the whole web's worth of rows to answer
# it. The bands are RANK_BANDS, the same ones the published rank facets use, so
# a shard is exactly one band: the file you download matches the figure you are
# checking.
#
# Bodies are NOT in these files -- a record carries status, headers, timings and
# a sha256 into the blob store, and the blob store is local-only.

RANK_BANDS = ((1, 1000), (1001, 2500), (2501, 5000), (5001, 10000), (10001, 20000))
RECORDS_DIR = "records"


def shard_name(rank: object) -> str:
    """The shard a record belongs in, chosen by its rank at panel freeze."""
    # bool is a subclass of int, and True would otherwise route to band 1.
    if isinstance(rank, int) and not isinstance(rank, bool) and rank > 0:
        for lo, hi in RANK_BANDS:
            if lo <= rank <= hi:
                return f"rank-{lo:05d}-{hi:05d}.jsonl"
        return "rank-beyond.jsonl"
    # A host whose probe raised before its rank was attached still has to land
    # somewhere; losing it silently would be worse than an odd filename.
    return "rank-unknown.jsonl"


def shard_paths(run_dir: Path) -> list[Path]:
    """Every records file for a run, in band order.

    Runs collected before sharding hold a single records.jsonl. It is still
    read, so published runs stay readable and no migration is load-bearing.
    """
    d = run_dir / RECORDS_DIR
    if d.is_dir():
        return sorted(d.glob("*.jsonl"))
    legacy = run_dir / "records.jsonl"
    return [legacy] if legacy.exists() else []


def has_records(run_dir: Path) -> bool:
    return bool(shard_paths(run_dir))


def load_records(run_dir: Path, tolerant: bool = False) -> list[dict]:
    """Read every record for a run.

    Strict by default: a line that will not parse is data loss, and derive
    should fail loudly rather than quietly report a share of fewer hosts than
    were probed. `tolerant` is for the two callers that legitimately meet a
    torn final line -- resume, and status on a run still being written.
    """
    recs: list[dict] = []
    for path in shard_paths(run_dir):
        for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not ln.strip():
                continue
            try:
                recs.append(json.loads(ln))
            except ValueError:
                if not tolerant:
                    sys.exit(f"{path}:{i}: unparseable record; refusing to "
                             f"derive from a truncated file")
    return recs


def write_records(run_dir: Path, recs: list[dict]) -> None:
    """Replace a run's records wholesale, re-sharding by rank.

    Used by retry and reparse, which rewrite records in place. Shards are
    cleared first so a record that changes band cannot survive in both.
    """
    d = run_dir / RECORDS_DIR
    d.mkdir(parents=True, exist_ok=True)
    for stale in d.glob("*.jsonl"):
        stale.unlink()
    by_shard: dict[str, list[dict]] = {}
    for r in recs:
        by_shard.setdefault(shard_name(r.get("rank_at_freeze")), []).append(r)
    for name, rows in by_shard.items():
        with (d / name).open("w", encoding="utf-8") as fh:
            for r in sorted(rows, key=lambda r: r["host"]):
                fh.write(json.dumps(r, sort_keys=True) + "\n")

    # Read back before dropping the single-file original. The shards are the
    # only copy the moment it is unlinked, so the check has to happen while
    # there is still something to fall back to.
    back = load_records(run_dir)
    if len(back) != len(recs) or {r["host"] for r in back} != {r["host"] for r in recs}:
        sys.exit(f"{run_dir}: re-reading the shards did not return what was "
                 f"written ({len(back)} of {len(recs)}); left the original in place")
    legacy = run_dir / "records.jsonl"
    if legacy.exists():
        legacy.unlink()


class ShardWriter:
    """Appends each record to the shard for its band, during a run.

    One handle per shard, held open: a run writes 16,314 records and opening
    the file per record would be 16,314 opens. Every write is flushed, because
    a run that dies halfway must leave usable partial data for --resume.
    """

    def __init__(self, run_dir: Path):
        self.dir = run_dir / RECORDS_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fhs: dict[str, object] = {}

    def write(self, rec: dict) -> None:
        name = shard_name(rec.get("rank_at_freeze"))
        fh = self.fhs.get(name)
        if fh is None:
            fh = self.fhs[name] = (self.dir / name).open("a", encoding="utf-8")
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
        fh.flush()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for fh in self.fhs.values():
            fh.close()


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
        sys.exit(f"no panel at {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def new_client(headers: dict) -> "httpx.Client":
    return httpx.Client(follow_redirects=True, max_redirects=MAX_REDIRECTS,
                        timeout=TIMEOUT, headers=headers)


def fetch(url: str, headers: dict, client=None) -> dict:
    """One polite GET, reusing the caller's client where given.

    A client per REQUEST was the original design, reasoning that a fresh
    cookie jar beat connection reuse against a 1.5s gap. That was wrong at
    scale: twelve paths per host meant twelve DNS lookups and twelve TLS
    handshakes, and at thirty workers that is up to 360 concurrent
    getaddrinfo calls against a local stub resolver. The resolver did not
    fail -- it stopped answering promptly, and no host ever finished its
    twelve paths. One client per host per identity makes that two lookups
    instead of twelve, and reusing a connection is gentler on the host too.

    Cookies are cleared after each request, so nothing carries between them.

    Retries once on a transport error and never on an HTTP status -- a 403 is
    data, not a failure.
    """
    out: dict = {"status": None, "ms": None, "bytes": None, "sha256": None,
                 "truncated": False, "redirects": 0, "final_host": None,
                 "final_path": None, "path_mismatch": False,
                 "headers": {}, "error": None, "dns_error": False}
    body: str | None = None
    owned = client is None
    if owned:
        client = new_client(headers)

    try:
        for attempt in (1, 2):
            t0 = time.monotonic()
            try:
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
                    # Every header is kept: an allowlist decides in advance
                    # which questions are answerable later, and that cannot be
                    # revisited without crawling again. Cookie VALUES are the
                    # one redaction -- an anonymous session id does not belong
                    # in a public archive.
                    out["headers"] = {
                        k.lower(): ("<present>" if k.lower() == "set-cookie" else v)
                        for k, v in resp.headers.items()
                    }
                    body = raw.decode("utf-8", errors="replace")
                client.cookies.clear()
                break
            except Exception as exc:
                # Deliberately broad. httpx.HTTPError is not the only thing a
                # hostile or malformed host can raise: quickconnect.to redirects
                # to a host with an empty DNS label and the IDNA codec raises
                # UnicodeEncodeError, which is not an HTTPError and so escaped
                # this handler entirely. A single such host stopped a
                # 16,000-host run for an hour.
                out["error"] = f"{type(exc).__name__}: {exc}"[:200]
                out["dns_error"] = robots_parse.is_dns_error(out["error"])
                if out["dns_error"] or attempt == 2:
                    break  # retrying a name that does not resolve is pointless
                time.sleep(1.0)
    finally:
        if owned:
            client.close()

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

    t_start = time.monotonic()
    clients = {i: new_client(h) for i, h in IDENTITIES.items()}
    bodies: dict[str, str | None] = {}
    first = True
    for ident, headers in IDENTITIES.items():
        if not first:
            time.sleep(HOST_GAP_S)
        first = False
        got = fetch(f"{SCHEME}://{host}/robots.txt", headers, clients[ident])
        body = got.pop("_body")
        bodies[ident] = body
        state = robots_parse.classify(got["status"], body,
                                      got["error"] is not None,
                                      got["path_mismatch"], got["dns_error"])
        clipped = store_blob(got["sha256"], body, present=(state == "present"))
        entry = {"identity": ident, **got, "file_state": state}
        if clipped:
            entry["archive_truncated"] = True
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
    # `is not None`, not truthiness: 104 hosts in panel-02 serve a zero-byte
    # robots.txt, which is a valid file meaning "no rules". Skipping the parse
    # left their policy as a bare file_state with every field missing rather
    # than explicitly empty -- same answer to every question asked so far, but
    # only by accident of absent and [] both being falsy.
    if honest["file_state"] == "present" and bodies["honest_bot"] is not None:
        rec["policy"].update(
            robots_parse.summarise(bodies["honest_bot"], watchlist, US_TOKEN)
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
        for c in clients.values():
            c.close()
        return rec

    for key, path in WELLKNOWN.items():
        if time.monotonic() - t_start > HOST_BUDGET_S:
            rec["wellknown"][key] = {"status": None, "file_state": "skipped",
                                     "skipped": "host_time_budget"}
            rec.setdefault("budget_exceeded", True)
            continue
        time.sleep(HOST_GAP_S)
        got = fetch(f"{SCHEME}://{host}{path}", IDENTITIES["honest_bot"],
                    clients["honest_bot"])
        body = got.pop("_body")
        state = robots_parse.classify(got["status"], body,
                                      got["error"] is not None,
                                      got["path_mismatch"], got["dns_error"],
                                      expect_json=key in JSON_WELLKNOWN,
                                      empty_is_absent=True)
        clipped = store_blob(got["sha256"], body, present=(state == "present"))
        rec["wellknown"][key] = {
            "status": got["status"], "file_state": state,
            "bytes": got["bytes"], "sha256": got["sha256"],
            "redirects": got["redirects"], "final_path": got["final_path"],
        }
        if clipped:
            rec["wellknown"][key]["archive_truncated"] = True
        if got["error"]:
            rec["errors"].append(f"{key}: {got['error']}")

    for c in clients.values():
        c.close()
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
    recs = load_records(run_dir)
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

    write_records(run_dir, [by_host[h] for h in sorted(by_host)])
    still = sorted(h for h, r in by_host.items() if r["errors"])
    print(f"  recovered {len(recovered)} of {len(failed)}; "
          f"{len(still)} still failing", file=sys.stderr)
    return {"attempted": len(failed), "recovered": sorted(recovered),
            "still_failing": still}


def cmd_retry(args) -> None:
    """Run the retry pass against the latest run and rewrite its metadata."""
    base = RUNS / PANEL_ID
    runs = sorted((d for d in base.iterdir()
                   if d.is_dir() and (d / "run-meta.json").exists()
                   and d.name != "dry-run"),
                  key=lambda d: d.stat().st_mtime) if base.exists() else []
    if not runs:
        sys.exit("no run to retry")
    run_dir = runs[-1]
    meta = json.loads((run_dir / "run-meta.json").read_text())
    panel = load_panel()
    res = retry_failed(run_dir, panel, load_watchlist(), meta["run_id"],
                       meta["vantage"], True, pause_s=args.pause)

    recs = load_records(run_dir)
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
    unres_share = len(unresolvable) / len(panel) if panel else 0.0
    if unresolvable and unres_share > UNRESOLVABLE_BUDGET and not args.allow_unresolvable:
        lines = "\n".join(f"    {h:<30}{e}"
                           for h, e in sorted(unresolvable.items())[:30])
        more = (f"\n    ... and {len(unresolvable) - 30} more"
                if len(unresolvable) > 30 else "")
        sys.exit(
            f"{len(unresolvable)} of {len(panel)} PANEL HOSTS DO NOT RESOLVE "
            f"({unres_share:.1%}, budget {UNRESOLVABLE_BUDGET:.0%})\n\n"
            f"{lines}{more}\n\n"
            "  A share this large is systematic, not transient: something is\n"
            "  removing a category of host rather than a few failing by chance.\n"
            "  Probing them would record them as unreachable, which reads as a\n"
            "  finding about those hosts rather than about this machine.\n\n"
            "  Exclude them with a documented reason and re-freeze, run from a\n"
            "  vantage that resolves them, or pass --allow-unresolvable."
        )
    if unresolvable:
        print(f"note: {len(unresolvable)} host(s) ({unres_share:.2%}) do not "
              f"resolve right now; within the {UNRESOLVABLE_BUDGET:.0%} budget, "
              f"so they are probed and recorded as dns_failure", file=sys.stderr)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    vantage = os.environ.get("OBSERVATORY_VANTAGE", "local")
    run_dir = RUNS / PANEL_ID / ("dry-run" if args.dry_run else run_id)
    meta_path = run_dir / "run-meta.json"
    if meta_path.exists() and not (args.resume or args.force or args.dry_run):
        prior = json.loads(meta_path.read_text())
        sys.exit(
            f"run {run_id} already exists ({prior.get('status')}, "
            f"{prior.get('hosts_ok')}/{prior.get('hosts_attempted')} clean).\n"
            "  Runs are append-only history. Use --resume to continue an "
            "interrupted one,\n  --run-id to name this one differently, or "
            "--force to overwrite deliberately."
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    # Two runs writing one records file produce duplicate hosts and double the
    # requests this project promises. An advisory lock makes that impossible
    # rather than merely unlikely; it releases automatically if the process
    # dies, so a killed run does not need cleaning up by hand.
    import fcntl
    lock_path = run_dir / ".lock"
    lock_fh = lock_path.open("w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(f"another run is already writing {run_dir}\n"
                 "  (lock held on .lock in that directory)")

    done_hosts: set[str] = set()
    if args.resume and has_records(run_dir):
        done_hosts = {r["host"] for r in load_records(run_dir, tolerant=True)
                      if r.get("host")}
        panel = [r for r in panel if r["host"].strip().lower() not in done_hosts]
        print(f"resuming: {len(done_hosts)} host(s) already recorded, "
              f"{len(panel)} to go", file=sys.stderr)
        if not panel:
            sys.exit("nothing left to do; run derive")
    else:
        # Not a resume, so any shard already here belongs to a previous attempt
        # at this run id. Appending to it would duplicate hosts; the writer only
        # ever appends, so they are cleared once, here.
        for stale in shard_paths(run_dir):
            stale.unlink()

    print(f"run {run_id} | {len(panel)} hosts | {len(panel) * (len(IDENTITIES) + len(WELLKNOWN)):,} requests | "
          f"vantage {vantage}", file=sys.stderr)
    started, t0 = utc_now(), time.monotonic()
    ok = 0
    lock = threading.Lock()

    # Append as each host completes: less code than accumulating, and a run that
    # dies halfway leaves usable partial data. A failed run is 14 minutes to
    # redo, so there is no resume machinery.
    # Keyed the same way probe_host reads it, so a record built on the error
    # path still lands in its real band rather than rank-unknown.
    ranks = {r["host"].strip().lower():
             (int(r["rank_at_freeze"]) if r.get("rank_at_freeze") else None)
             for r in panel}
    with ShardWriter(run_dir) as fh:
        with ThreadPoolExecutor(CONCURRENT_HOSTS) as pool:
            futures = {
                pool.submit(probe_host, r, watchlist, run_id, vantage,
                            not args.dry_run): r["host"].strip().lower()
                for r in panel
            }
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    rec = fut.result()
                except Exception as exc:
                    # Record the failure and carry on. Letting this propagate
                    # kills the loop, and `with ThreadPoolExecutor` then waits
                    # for every queued task before surfacing it -- the run
                    # appears to hang with threads busy and nothing written.
                    rec = {"run_id": run_id, "panel_id": PANEL_ID,
                           "schema": SCHEMA, "host": futures[fut],
                           "rank_at_freeze": ranks.get(futures[fut]),
                           "vantage": vantage, "robots": [], "wellknown": {},
                           "policy": {"file_state": "error"},
                           "errors": [f"probe raised {type(exc).__name__}: {exc}"[:200]]}
                with lock:
                    fh.write(rec)
                    if not rec["errors"]:
                        ok += 1
                    if i % 25 == 0 or i == len(futures):
                        print(f"  {i}/{len(futures)}  ok={ok}", file=sys.stderr)

    all_recs = load_records(run_dir)
    attempted = len(all_recs)
    ok = sum(1 for r in all_recs if not r["errors"])
    err_rate = 0.0 if not attempted else (attempted - ok) / attempted
    reasons = []
    if err_rate > ERROR_BUDGET:
        reasons.append(f"error_rate_{err_rate:.3f}")
    if unres_share > UNRESOLVABLE_BUDGET:
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
          f"{meta['duration_s']}s -> {run_dir / RECORDS_DIR}", file=sys.stderr)
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


def catch_all_shas(rec: dict) -> set:
    """Hashes a host returns for more than one distinct well-known path.

    A host that answers every unknown path with the same body is not
    publishing those files; it has a catch-all. cloudflare.net returns
    "Invalid key" for agent-card.json, ai-catalog.json, agent.json, gpc.json
    and tdmrep.json alike -- eleven bytes, one hash, five apparent adoptions.

    Computed at derivation rather than collection, so it applies to data
    already gathered and can be revised without re-crawling. Distinct real
    policy files never share a body.
    """
    # Counted over every path that returned a body, not only those still
    # classified present. Tying it to 'present' made the detector depend on
    # its own downstream result: demoting cloudflare.net's five non-JSON
    # agent files dropped it below the threshold and un-discounted the sixth
    # copy of the same "Invalid key" body, which then counted as a real
    # security.txt.
    seen: dict[str, int] = {}
    for v in (rec.get("wellknown") or {}).values():
        if v.get("sha256"):
            seen[v["sha256"]] = seen.get(v["sha256"], 0) + 1
    return {sha for sha, n in seen.items() if n > 1}


def tdm_reserves(body: str | None) -> bool | None:
    """Does this tdmrep.json actually reserve rights?

    Presence is not stance. TDMRep expresses either a reservation
    (`tdm-reservation: 1`) or an explicit grant (`0`); tiket.com publishes a
    grant. Counting every tdmrep.json as an opt-out would file a permission
    under refusals.
    """
    if body is None:
        return None
    try:
        d = json.loads(body)
    except ValueError:
        return None
    items = d if isinstance(d, list) else [d]
    return any(isinstance(i, dict) and i.get("tdm-reservation", 0) not in (0, None)
               for i in items)


def ai_txt_restricts(body: str | None) -> bool | None:
    """Does this ai.txt withhold anything, or is it a guide?

    ai.txt is used both ways. Most are Spawning-style opt-outs listing
    `Disallow:` rules; a handful (manuals.plus, vidiq.com, att.com) are
    documents pointing AI systems at what they may use, which is the opposite
    stance in the same file.
    """
    if body is None:
        return None
    for line in body.splitlines():
        line = line.strip()
        if line.lower().startswith("disallow:") and line.split(":", 1)[1].strip():
            return True
    return False


def _catch_all_suppressed(rec: dict) -> bool:
    """True if a repeated body cost this host at least one apparent file."""
    shas = catch_all_shas(rec)
    if not shas:
        return False
    return any((v or {}).get("file_state") == "present"
               and (v or {}).get("sha256") in shas
               for v in (rec.get("wellknown") or {}).values())


def wk_present(rec: dict, key: str) -> bool:
    """Did this host really publish this well-known file?"""
    v = (rec.get("wellknown") or {}).get(key, {})
    if v.get("file_state") != "present":
        return False
    return v.get("sha256") not in catch_all_shas(rec)


def _policy_varies(r: dict) -> bool:
    return robots_parse.policy_varies(r.get("robots", []), len(IDENTITIES))


def _access_varies(r: dict) -> bool:
    return robots_parse.access_varies(r.get("robots", []), len(IDENTITIES))


def _load_run(run_id: str | None = None) -> tuple[Path, list[dict]]:
    base = RUNS / PANEL_ID
    runs = sorted((d for d in base.iterdir()
                   if d.is_dir() and has_records(d) and d.name != "dry-run"),
                  key=lambda d: d.stat().st_mtime) if base.exists() else []
    if not runs:
        sys.exit(f"no runs for {PANEL_ID}")
    run_dir = (base / run_id) if run_id else runs[-1]
    if not has_records(run_dir):
        sys.exit(f"no such run: {run_dir}")
    return run_dir, load_records(run_dir)


def _file_sha(rec: dict, which: str) -> str | None:
    if which == "robots":
        for e in rec.get("robots", []):
            if e.get("identity") == "honest_bot" and e.get("file_state") == "present":
                return e.get("sha256")
        return None
    w = rec.get("wellknown", {}).get(which, {})
    return w.get("sha256") if w.get("file_state") == "present" else None


def cmd_cat(args) -> None:
    """Print a stored file for one host."""
    _, recs = _load_run(args.run)
    rec = next((r for r in recs if r["host"] == args.host), None)
    if not rec:
        sys.exit(f"{args.host} not in this run")
    sha = _file_sha(rec, args.file)
    if not sha:
        sys.exit(f"{args.host}: no stored {args.file} "
                 f"(state: {rec.get('policy', {}).get('file_state')})")
    body = read_blob(sha)
    if body is None:
        sys.exit(f"blob {sha[:12]} missing")
    print(body, end="")


def cmd_diff(args) -> None:
    """Diff one host's stored file between two runs."""
    import difflib
    _, a = _load_run(args.from_run)
    _, b = _load_run(args.to_run)
    ra = next((r for r in a if r["host"] == args.host), None)
    rb = next((r for r in b if r["host"] == args.host), None)
    if not ra or not rb:
        sys.exit(f"{args.host} missing from one of the runs")
    sa, sb = _file_sha(ra, args.file), _file_sha(rb, args.file)
    if sa == sb:
        print(f"{args.host} {args.file}: unchanged ({(sa or 'absent')[:12]})",
              file=sys.stderr)
        return
    ta = (read_blob(sa) or "") if sa else ""
    tb = (read_blob(sb) or "") if sb else ""
    sys.stdout.writelines(difflib.unified_diff(
        ta.splitlines(keepends=True), tb.splitlines(keepends=True),
        fromfile=f"{args.host} {ra['run_id']}", tofile=f"{args.host} {rb['run_id']}"))


def cmd_corpus(args) -> None:
    """List stored files of one kind, for bulk analysis of the corpus."""
    run_dir, recs = _load_run(args.run)
    rows = [(r["host"], _file_sha(r, args.file)) for r in recs]
    rows = [(h, s) for h, s in rows if s]
    print(f"{len(rows)} stored {args.file} files in {run_dir.name}", file=sys.stderr)
    for h, sha in sorted(rows):
        print(f"{h}\t{blob_path(sha)}")


def cmd_reparse(args) -> None:
    """Re-derive every record's parsed policy from the archived bodies.

    This is what the archive is for. A parser fix, or a question nobody asked
    at collection time, is applied backwards across the whole history instead
    of invalidating it -- so week 40 stays comparable with week 1.
    """
    base = RUNS / PANEL_ID
    runs = sorted(d for d in base.iterdir()
                  if d.is_dir() and has_records(d)) if base.exists() else []
    if not runs:
        sys.exit(f"no runs for {PANEL_ID}")
    watchlist = load_watchlist()
    for run_dir in runs:
        recs = load_records(run_dir)
        changed = missing = demoted = clipped_marked = 0
        for r in recs:
            # A JSON convention whose archived body is not JSON was never that
            # file. Only ever demotes a 'present', so a reclassification can
            # lower a published share and never inflate one.
            for key in (r.get("wellknown") or {}):
                if key not in JSON_WELLKNOWN:
                    continue
                wk = r["wellknown"][key] or {}
                if wk.get("file_state") != "present" or not wk.get("sha256"):
                    continue
                body = read_blob(wk["sha256"])
                if body is None or robots_parse.looks_like_json(body):
                    continue
                wk["file_state"] = "soft_404"
                wk["reclassified"] = "body is not JSON"
                demoted += 1

            # And a zero-length body at any well-known path.
            for key in (r.get("wellknown") or {}):
                wk = r["wellknown"][key] or {}
                if wk.get("file_state") != "present":
                    continue
                if wk.get("bytes") == 0 or (
                        wk.get("sha256") and (read_blob(wk["sha256"]) or "").strip() == ""):
                    wk["file_state"] = "empty"
                    wk["reclassified"] = "zero-length body"
                    demoted += 1

            # A body stored before truncation was recorded cannot hash to its
            # own filename. Detect that here and say so on the record, so the
            # archive describes itself instead of looking corrupt.
            for entry in list(r.get("robots") or []) + \
                    [v for v in (r.get("wellknown") or {}).values() if v]:
                sha = entry.get("sha256")
                if not sha or entry.get("archive_truncated"):
                    continue
                bp = blob_path(sha)
                if not bp.exists():
                    continue
                if hashlib.sha256(bp.read_bytes()).hexdigest() != sha:
                    entry["archive_truncated"] = True
                    clipped_marked += 1

            # Stance that can only be read from the body, stamped onto the
            # record so derive stays a pure function of the records and works
            # without the blob store.
            for key, fn in (("tdmrep", tdm_reserves), ("ai_txt", ai_txt_restricts)):
                wk = (r.get("wellknown") or {}).get(key) or {}
                if wk.get("file_state") == "present" and wk.get("sha256"):
                    verdict = fn(read_blob(wk["sha256"]))
                    if verdict is not None:
                        wk["restricts"] = verdict

            sha = _file_sha(r, "robots")
            if not sha:
                r["parser_version"] = robots_parse.PARSER_VERSION
                continue
            body = read_blob(sha)
            if body is None:
                missing += 1
                continue
            fresh = robots_parse.summarise(body, watchlist, US_TOKEN)
            fresh["file_state"] = r["policy"]["file_state"]
            if fresh != r["policy"]:
                changed += 1
            r["policy"] = fresh
            r["parser_version"] = robots_parse.PARSER_VERSION
        if not args.dry_run:
            write_records(run_dir, recs)
        print(f"  {run_dir.name}: {changed} policy record(s) changed, "
              f"{demoted} non-JSON well-known file(s) demoted, "
              f"{clipped_marked} truncated blob(s) marked"
              + (f", {missing} body missing" if missing else "")
              + (" (dry run)" if args.dry_run else ""), file=sys.stderr)
    print(f"reparsed at parser_version={robots_parse.PARSER_VERSION}", file=sys.stderr)




def _wk_restricts(rec: dict, key: str) -> bool:
    """A convention counted as restrictive only where its body says so."""
    if not wk_present(rec, key):
        return False
    return bool(((rec.get("wellknown") or {}).get(key) or {}).get("restricts"))


def _wk_permits(rec: dict, key: str) -> bool:
    """Published, and its body does not withhold anything."""
    if not wk_present(rec, key):
        return False
    wk = (rec.get("wellknown") or {}).get(key) or {}
    return "restricts" in wk and not wk["restricts"]


# What a host declares toward automated readers, as separate measures rather
# than one axis. A site can document itself and block a crawler in the same
# breath, and collapsing that into a single "friendliness" score would hide
# the most interesting group on the web.
SUPPORT_SIGNALS = {
    "llms_txt": lambda r: wk_present(r, "llms_txt"),
    "agent_discovery": lambda r: any(wk_present(r, k) for k in
                                     ("agent_card", "agent_card_legacy", "ai_catalog")),
    "ai_txt_as_guide": lambda r: _wk_permits(r, "ai_txt"),
    "names_ai_blocks_none": lambda r: bool(_pol(r, "ai_tokens_named"))
                                      and not _pol(r, "ai_tokens_root_disallowed")
                                      and not _pol(r, "wildcard_root_disallowed"),
}
HINDER_SIGNALS = {
    "blocks_named_ai": lambda r: bool(_pol(r, "ai_tokens_root_disallowed")),
    "blocks_all_crawlers": lambda r: bool(_pol(r, "wildcard_root_disallowed")),
    "reserves_tdm": lambda r: _wk_restricts(r, "tdmrep"),
    "ai_txt_optout": lambda r: _wk_restricts(r, "ai_txt"),
    "asks_for_money": lambda r: bool(_pol(r, "rsl_license_refs")),
}
FRICTION_SIGNALS = {
    "crawl_delay": lambda r: _pol(r, "crawl_delay") not in (None, "", "0"),
    "policy_by_identity": lambda r: bool(r.get("policy_varies_by_identity")),
    "access_by_identity": lambda r: bool(r.get("access_varies_by_identity")),
}


def _pol(r: dict, key: str, default=None):
    return (r.get("policy") or {}).get(key, default)


def _stance(recs: list[dict]) -> dict:
    """Support / silence / hindrance, over hosts that gave a definitive answer.

    The denominator is deliberately not every host probed. A host that refused
    us, timed out or sent us to a consent wall has no measurable stance, and
    counting it as "silent" would read as indifference where the truth is that
    we could not tell.
    """
    answered = [r for r in recs
                if _pol(r, "file_state") in ("present", "absent_404")]
    n = len(answered)
    if not n:
        return {}

    def tally(group):
        return {k: {"n": c, "pct": round(100 * c / n, 2)}
                for k, f in group.items()
                for c in [sum(1 for r in answered if f(r))]}

    sup = [any(f(r) for f in SUPPORT_SIGNALS.values()) for r in answered]
    hin = [any(f(r) for f in HINDER_SIGNALS.values()) for r in answered]
    cells = {"supports_only": 0, "both": 0, "restricts_only": 0, "silent": 0}
    for s_, h_ in zip(sup, hin):
        cells["both" if s_ and h_ else
              "supports_only" if s_ else
              "restricts_only" if h_ else "silent"] += 1
    return {
        "answered": n,
        "unreadable": len(recs) - n,
        "stance": {k: {"n": v, "pct": round(100 * v / n, 2)} for k, v in cells.items()},
        "support": tally(SUPPORT_SIGNALS),
        "hindrance": tally(HINDER_SIGNALS),
        "friction": tally(FRICTION_SIGNALS),
    }


def _lens_figures(recs: list[dict], sectors: dict) -> list[dict]:
    """The one number each lens leads with.

    These are ordinary statistics, not page furniture: they live here so the
    findings page can preview the lenses without loading the per-site file,
    and so there is one place they are computed rather than two.
    """
    rd = [r for r in recs if _pol(r, "file_state") in ("present", "absent_404")]
    n = len(rd) or 1
    present = [r for r in rd if _pol(r, "file_state") == "present"]

    def rate(purpose):
        a = b = 0
        for r in present:
            named = set(_pol(r, "ai_tokens_named") or [])
            blocked = set(_pol(r, "ai_tokens_root_disallowed") or [])
            for t in named:
                if AI_PURPOSE.get(t) == purpose:
                    a += 1
                    if t in blocked:
                        b += 1
        return round(100 * b / a, 1) if a else 0.0

    split_a = split_b = 0
    for r in present:
        named = [t for t in (_pol(r, "ai_tokens_named") or [])
                 if AI_OPERATOR.get(t) == "OpenAI"]
        if len(named) < 2:
            continue
        blocked = set(_pol(r, "ai_tokens_root_disallowed") or [])
        hit = sum(1 for t in named if t in blocked)
        split_a += 1
        if 0 < hit < len(named):
            split_b += 1

    ghosts = sum(1 for r in present if _ghosts(r, {w.lower() for w in load_watchlist()}))
    own = sum(1 for r in present if _pol(r, "has_legal_text")
              and not _pol(r, "legal_boilerplate"))
    lines_ai = sorted(_pol(r, "directive_lines") or 0 for r in present
                      if _pol(r, "ai_tokens_named"))
    lines_no = sorted(_pol(r, "directive_lines") or 0 for r in present
                      if not _pol(r, "ai_tokens_named"))
    mid = lambda a: a[len(a) // 2] if a else 0
    # Counted over every host, as the top-level `wellknown` block is: a site
    # can publish llms.txt while refusing us its robots.txt, and filtering to
    # readable rulebooks would undercount adoption.
    formal = sum(1 for r in recs if any(wk_present(r, k) for k in
                 ("tdmrep", "ai_catalog", "agent_card", "agent_card_legacy")))
    llms = sum(1 for r in recs if wk_present(r, "llms_txt"))
    tdm = sum(1 for r in recs if wk_present(r, "tdmrep"))
    both = sum(1 for r in rd
               if any(f(r) for f in SUPPORT_SIGNALS.values())
               and any(f(r) for f in HINDER_SIGNALS.values()))
    silent = sum(1 for r in rd
                 if not any(f(r) for f in SUPPORT_SIGNALS.values())
                 and not any(f(r) for f in HINDER_SIGNALS.values()))

    def pc(v):
        return f"{v:.0f}%"

    # A small real series per lens, so the findings page can draw a true
    # miniature of each chart instead of a decorative glyph. Kept to a handful
    # of numbers: the point is the shape, not the detail.
    bands = _rank_bands(recs)
    ghost_counts: dict[str, int] = {}
    for r in present:
        for g in _ghosts(r, {w.lower() for w in load_watchlist()}):
            k = g.lower()
            ghost_counts[k] = ghost_counts.get(k, 0) + 1
    top_ghosts = sorted(ghost_counts.values(), reverse=True)[:5]

    ops = []
    for op in ("OpenAI", "Anthropic", "Meta", "Perplexity", "Webz.io"):
        a = b = 0
        for r in present:
            named = [t for t in (_pol(r, "ai_tokens_named") or [])
                     if AI_OPERATOR.get(t) == op]
            if len(named) < 2:
                continue
            blocked = set(_pol(r, "ai_tokens_root_disallowed") or [])
            hit = sum(1 for t in named if t in blocked)
            a += 1
            if 0 < hit < len(named):
                b += 1
        ops.append(round(100 * b / a, 1) if a else 0.0)

    legal_own, legal_tpl = [], []
    for i, _ in enumerate(RANK_BANDS):
        grp = [r for r in rd if (lambda k: k is not None and
               RANK_BANDS[i][0] <= k <= RANK_BANDS[i][1])(r.get("rank_at_freeze"))]
        n2 = len(grp) or 1
        legal_own.append(round(100 * sum(
            1 for r in grp if _pol(r, "has_legal_text")
            and not _pol(r, "legal_boilerplate")) / n2, 2))
        legal_tpl.append(round(100 * sum(
            1 for r in grp if _pol(r, "legal_boilerplate")) / n2, 2))

    silent_by_band = []
    for lo, hi in RANK_BANDS:
        grp = [r for r in rd if (r.get("rank_at_freeze") or 0) and
               lo <= r["rank_at_freeze"] <= hi]
        n2 = len(grp) or 1
        silent_by_band.append(round(100 * sum(
            1 for r in grp
            if not any(f(r) for f in SUPPORT_SIGNALS.values())
            and not any(f(r) for f in HINDER_SIGNALS.values())) / n2, 1))

    sec_rows = [(k, v) for k, v in sorted(
        sectors.items(), key=lambda kv: -(kv[1]["withholds"] / max(kv[1]["hosts"], 1)))
        if k != "unlabelled"][:5]
    content_signal = sum(1 for r in present if _pol(r, "content_signal"))

    prev = {
        "purpose":   {"kind": "ladder",   "v": [rate("train"), rate("search"), rate("user")]},
        "operators": {"kind": "dumbbell", "v": ops},
        "sector":    {"kind": "diverge",
                      "v": [round(100 * v["withholds"] / v["hosts"], 1) for _, v in sec_rows],
                      "w": [round(100 * v["publishes"] / v["hosts"], 1) for _, v in sec_rows]},
        "fork":      {"kind": "venn",
                      "v": [round(100 * sum(1 for r in rd if any(f(r) for f in SUPPORT_SIGNALS.values())) / n, 1),
                            round(100 * both / n, 1),
                            round(100 * sum(1 for r in rd if any(f(r) for f in HINDER_SIGNALS.values())) / n, 1)]},
        "signal":    {"kind": "blocks",
                      "v": [llms, content_signal,
                            sum(1 for r in recs if wk_present(r, "tdmrep")),
                            sum(1 for r in recs if wk_present(r, "ai_catalog")),
                            sum(1 for r in recs if wk_present(r, "ai_txt"))]},
        "tdm":       {"kind": "dots",     "v": [17, 11, 7, 11]},
        "ghosts":    {"kind": "hollow",   "v": top_ghosts},
        "legal":     {"kind": "stack",    "v": legal_own, "w": legal_tpl},
        "strain":    {"kind": "pair",     "v": [mid(lines_ai), mid(lines_no)]},
        "silence":   {"kind": "grid",     "v": silent_by_band},
    }

    rows = [
        {"id": "purpose", "figure": pc(rate("train")),
         "label": "of training crawlers are blocked where they are named"},
        {"id": "operators",
         "figure": f"{100 * split_b / split_a:.1f}%" if split_a else "\u2014",
         "label": "of sites split OpenAI's bots, allowing some and blocking others"},
        {"id": "sector",
         "figure": (f"{100 * sectors['news']['withholds'] / sectors['news']['hosts']:.0f}%"
                    if sectors.get("news", {}).get("hosts") else None),
         "label": "of newsrooms withhold something; developer sites mostly publish"},
        {"id": "fork", "figure": f"{both:,}",
         "label": "sites both document themselves and withhold something"},
        {"id": "signal", "figure": f"{round(llms / formal) if formal else 0}\u00d7",
         "label": "more llms.txt than every formally specified convention combined"},
        {"id": "tdm", "figure": f"{tdm:,}",
         "label": "sites publish a machine-readable TDM reservation"},
        {"id": "ghosts", "figure": f"{ghosts:,}",
         "label": "sites carry a rule that can never fire"},
        {"id": "legal", "figure": f"{own:,}",
         "label": "sites wrote their own terms into robots.txt"},
        {"id": "strain",
         "figure": (f"{mid(lines_ai) / mid(lines_no):.1f}\u00d7" if mid(lines_no) else "\u2014"),
         "label": "more directives once a rulebook mentions AI"},
        {"id": "silence", "figure": pc(100 * silent / n),
         "label": "of readable rulebooks never mention AI either way"},
    ]
    for row in rows:
        row["preview"] = prev.get(row["id"])
    return rows


def _rank_bands(recs: list[dict]) -> list[dict]:
    # Only paths this run actually requested, matching the top-level wellknown
    # summary. A run predating a path would otherwise report it at 0.0%, which
    # reads as "nobody publishes this" when it means "never asked".
    probed = [k for k in WELLKNOWN
              if any(k in r.get("wellknown", {}) for r in recs)]
    out = []
    for lo, hi in RANK_BANDS:
        sub = [r for r in recs if lo <= int(r.get("rank_at_freeze") or 0) <= hi]
        if not sub:
            continue
        pres = [r for r in sub if r.get("policy", {}).get("file_state") == "present"]
        row = {"lo": lo, "hi": hi, "hosts": len(sub), "readable": len(pres)}
        for k in probed:
            row[k] = round(100 * sum(1 for r in sub if wk_present(r, k)) / len(sub), 2)
        row["names_ai"] = round(
            100 * sum(1 for r in pres if r["policy"].get("ai_tokens_named")) / len(pres), 2
        ) if pres else 0.0
        out.append(row)
    return out


def cmd_verify(args) -> None:
    """Check that every archived body still is what the record says it is.

    A blob is named for the sha256 of the whole response. Bodies over the
    archive cap are stored as a prefix, so they cannot hash to their own name
    and an integrity check must be told which ones those are. Without that
    distinction the only available answer was "39% of this archive looks
    corrupt", which is useless.
    """
    base = RUNS / PANEL_ID
    runs = sorted(d for d in base.iterdir()
                  if d.is_dir() and has_records(d)) if base.exists() else []
    if not runs:
        sys.exit(f"no runs for {PANEL_ID}")
    intact = clipped = missing = missing_present = bad = 0
    examples: list[str] = []
    gaps: list[str] = []
    for run_dir in runs:
        for r in load_records(run_dir):
            entries = [(e, e.get("sha256")) for e in (r.get("robots") or [])]
            entries += [(v, (v or {}).get("sha256"))
                        for v in (r.get("wellknown") or {}).values()]
            for entry, sha in entries:
                if not sha:
                    continue
                path = blob_path(sha)
                if not path.exists():
                    # A body we never stored is a gap, but only a real loss
                    # where the response was a file. Runs collected before
                    # archiving covered every response are missing their 404s
                    # and 403s by design, and saying so separately keeps the
                    # number meaningful.
                    if entry.get("file_state") == "present":
                        missing_present += 1
                        if len(gaps) < 5:
                            gaps.append(f"{r['host']} {sha[:12]}")
                    else:
                        missing += 1
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest == sha:
                    intact += 1
                elif entry.get("archive_truncated"):
                    clipped += 1
                else:
                    bad += 1
                    if len(examples) < 5:
                        examples.append(f"{r['host']} {sha[:12]} "
                                        f"({path.stat().st_size:,} bytes on disk)")
    total = intact + clipped + bad
    print(f"{PANEL_ID}: {total:,} archived bodies", file=sys.stderr)
    print(f"  {intact:,} hash to their own name", file=sys.stderr)
    print(f"  {clipped:,} are declared truncated prefixes", file=sys.stderr)
    print(f"  {missing:,} absent, but were not files (404, 403, a redirect)",
          file=sys.stderr)
    if missing_present:
        print(f"  {missing_present:,} absent and WERE files: the body was never stored",
              file=sys.stderr)
        for g in gaps:
            print(f"      {g}", file=sys.stderr)
    if bad:
        print(f"  {bad:,} DO NOT MATCH and are not declared truncated:", file=sys.stderr)
        for e in examples:
            print(f"      {e}", file=sys.stderr)
        sys.exit("archive integrity check failed")
    print("  archive verifies", file=sys.stderr)


def cmd_reshard(args) -> None:
    """Split a run's records.jsonl into one file per rank band, in place.

    The migration for runs collected before sharding. It only moves rows
    between files: `derive` must produce byte-identical findings afterwards,
    and that is the check worth running, not this command's own output.
    """
    base = RUNS / PANEL_ID
    runs = sorted(d for d in base.iterdir()
                  if d.is_dir() and has_records(d)) if base.exists() else []
    if not runs:
        sys.exit(f"no runs for {PANEL_ID}")
    for run_dir in runs:
        if (run_dir / RECORDS_DIR).is_dir():
            print(f"  {run_dir.name}: already sharded", file=sys.stderr)
            continue
        recs = load_records(run_dir)
        hosts = len({r["host"] for r in recs})
        if args.dry_run:
            counts: dict[str, int] = {}
            for r in recs:
                nm = shard_name(r.get("rank_at_freeze"))
                counts[nm] = counts.get(nm, 0) + 1
            print(f"  {run_dir.name}: {len(recs)} record(s), {hosts} host(s) ->",
                  file=sys.stderr)
            for nm in sorted(counts):
                print(f"      {nm:26} {counts[nm]:6,}", file=sys.stderr)
            continue
        write_records(run_dir, recs)  # verifies the round trip before unlinking
        print(f"  {run_dir.name}: {len(recs)} record(s) -> "
              f"{len(shard_paths(run_dir))} shard(s)", file=sys.stderr)


# AI-shaped agent tokens that match no crawler anyone operates. Blocking one
# is a rule that can never fire: Claude-Web was retired, and a product token
# containing a space cannot match under RFC 9309 at all.
GHOST_RE = re.compile(
    r"(gpt|openai|chatgpt|claude|anthropic|llm|\bai\b|perplexity|gemini|bard"
    r"|copilot|mistral|llama)", re.I)


def _ghosts(rec: dict, watch_lower: set[str]) -> list[str]:
    out = []
    for t in (rec.get("policy") or {}).get("all_agent_tokens_seen", []) or []:
        if t == "*" or t.lower() in watch_lower:
            continue
        if GHOST_RE.search(t):
            out.append(t)
    return sorted(out)


def write_sites(recs: list[dict], watchlist: list[str]) -> dict:
    """One row per site, small enough to load in a browser.

    The findings file carries the aggregates; this carries the population they
    were computed from, so a reader can re-cut the data instead of taking the
    page's word for it. Columns are indices into the vocabularies above them,
    which keeps 16,314 rows near a megabyte rather than near ten.
    """
    wl = list(watchlist)
    wl_ix = {t.lower(): i for i, t in enumerate(wl)}
    wk_keys = list(WELLKNOWN)
    states: list[str] = []
    sectors: list[str] = []
    ghost_vocab: list[str] = []

    def ix(lst: list[str], v: str) -> int:
        if v not in lst:
            lst.append(v)
        return lst.index(v)

    rows = []
    for r in sorted(recs, key=lambda r: r.get("rank_at_freeze") or 10 ** 9):
        pol = r.get("policy") or {}
        named = sorted(wl_ix[t.lower()] for t in (pol.get("ai_tokens_named") or [])
                       if t.lower() in wl_ix)
        blocked = sorted(wl_ix[t.lower()] for t in (pol.get("ai_tokens_root_disallowed") or [])
                         if t.lower() in wl_ix)
        wk = sorted(i for i, k in enumerate(wk_keys) if wk_present(r, k))
        flags = 0
        for bit, on in enumerate((
            bool(pol.get("wildcard_root_disallowed")),
            bool(pol.get("rsl_license_refs")),
            bool(r.get("policy_varies_by_identity")),
            bool(r.get("access_varies_by_identity")),
            bool(pol.get("has_legal_text")),
            bool(pol.get("content_signal")),
            bool(pol.get("starts_with_bom")),
            bool(r.get("budget_exceeded")),
            bool(r.get("adult")),
            bool(_wk_restricts(r, "tdmrep")),
            bool(_wk_restricts(r, "ai_txt")),
            bool(pol.get("legal_boilerplate")),
        )):
            if on:
                flags |= 1 << bit
        rows.append([
            r["host"],
            r.get("rank_at_freeze"),
            ix(states, pol.get("file_state") or "unknown"),
            ix(sectors, (r.get("sector") or "").strip().lower() or "unlabelled"),
            named, blocked, wk, flags,
            pol.get("crawl_delay"),
            pol.get("sitemaps") or 0,
            pol.get("directive_lines") or 0,
            next((e.get("bytes") or 0 for e in (r.get("robots") or [])
                  if e.get("identity") == "honest_bot"), 0),
            sorted(ix(ghost_vocab, g) for g in _ghosts(r, set(wl_ix))),
            (pol.get("content_signal") or [None])[0],
        ])
    return {
        "panel_id": PANEL_ID,
        "generated": utc_now(),
        "note": "One row per site. Index columns point into the vocabularies "
                "below. Companion to findings; same run, same records.",
        "columns": ["host", "rank", "state", "sector", "named", "blocked",
                    "wellknown", "flags", "crawl_delay", "sitemaps",
                    "directive_lines", "bytes", "ghosts", "content_signal"],
        "flag_bits": ["blocks_all_crawlers", "asks_for_money",
                      "policy_varies_by_identity", "access_varies_by_identity",
                      "has_legal_text", "has_content_signal", "starts_with_bom",
                      "budget_exceeded", "adult", "reserves_tdm", "ai_txt_optout",
                      "legal_boilerplate"],
        "tokens": wl, "wellknown": wk_keys, "states": states,
        "sectors": sectors, "ghost_tokens": ghost_vocab,
        "sites": rows,
    }


def cmd_derive(args) -> None:
    base = RUNS / PANEL_ID
    runs = sorted((d for d in base.iterdir()
                   if d.is_dir() and has_records(d) and d.name != "dry-run"),
                  key=lambda d: d.stat().st_mtime) if base.exists() else []
    if not runs:
        sys.exit("no runs to derive from")
    run_dir = runs[-1]
    recs = load_records(run_dir)
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
        e = sectors.setdefault(s, {"hosts": 0, "names_ai": 0, "wildcard_block": 0,
                                   "llms_txt": 0, "withholds": 0, "publishes": 0})
        e["hosts"] += 1
        if any(f(r) for f in HINDER_SIGNALS.values()):
            e["withholds"] += 1
        if any(f(r) for f in SUPPORT_SIGNALS.values()):
            e["publishes"] += 1
        if pol(r, "ai_tokens_named"):
            e["names_ai"] += 1
        if pol(r, "wildcard_root_disallowed"):
            e["wildcard_block"] += 1
        if wk_present(r, "llms_txt"):
            e["llms_txt"] += 1

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
            k: share(lambda r, k=k: wk_present(r, k))
            for k in WELLKNOWN
            # only report a path this run actually requested
            if any(k in r.get("wellknown", {}) for r in recs)
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
        # Hosts too slow to finish their probe inside the budget. Their
        # remaining paths are unmeasured, not absent.
        "budget_exceeded": share(lambda r: bool(r.get("budget_exceeded"))),
        # Hosts where a repeated body actually suppressed a file that would
        # otherwise have counted. Not simply "some hash repeats": most hosts
        # serve one 404 page for every missing path, which is ordinary and
        # suppresses nothing. The detector considers every response (so it
        # cannot be weakened by its own demotions); this counts the hosts it
        # changed the answer for.
        "catch_all_responses": share(_catch_all_suppressed),
        # Hosts where at least one of the twelve requests failed at the
        # transport layer. Published so the page can state its own error rate
        # rather than carry a figure typed in by hand.
        "transport_errors": share(lambda r: bool(r.get("errors"))),
        "declared_stance": _stance(recs),
        "opted_out_via_robots": share(
            lambda r: bool(r.get("opted_out_via_robots"))
        ),
        "wildcard_blocks_us": share(lambda r: bool(r.get("wildcard_blocks_us"))),
        "challenged": share(lambda r: pol(r, "file_state") == "challenged"),
        "redirected_away": share(lambda r: pol(r, "file_state") == "redirected_away"),
        "by_sector": sectors,
        # Adoption against popularity rank. The single most important
        # correction this dataset makes: a rate measured on the top 1000 is
        # not a rate for the web, and these bands show how far off it is.
        "by_rank_band": _rank_bands(recs),
        "lenses": _lens_figures(recs, sectors),
    }

    SITE_DATA.mkdir(parents=True, exist_ok=True)
    # The population behind every figure above, small enough to load in a
    # browser so a reader can re-cut it instead of trusting the summary.
    (SITE_DATA / f"sites-{PANEL_ID}.json").write_text(
        json.dumps(write_sites(recs, load_watchlist()), separators=(",", ":")) + "\n",
        encoding="utf-8")
    (SITE_DATA / ("findings.json" if PANEL_ID == "panel-01" else f"findings-{PANEL_ID}.json")).write_text(
        json.dumps(findings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    flat = DATA / ("hosts.csv" if PANEL_ID == "panel-01" else f"hosts-{PANEL_ID}.csv")
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
                "present" if wk_present(r, "llms_txt") else wk.get("llms_txt", {}).get("file_state"),
                "present" if wk_present(r, "ai_txt") else wk.get("ai_txt", {}).get("file_state"),
                "present" if wk_present(r, "tdmrep") else wk.get("tdmrep", {}).get("file_state"),
                r.get("robots_varies_by_identity"),
            ])

    print(json.dumps(findings, indent=2, sort_keys=True))
    print(f"\nwrote findings for {PANEL_ID} and {flat}", file=sys.stderr)


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
    r.add_argument("--panel", default="panel-01")
    r.add_argument("--limit", type=int)
    r.add_argument("--host")
    r.add_argument("--run-id")
    r.add_argument("--resume", action="store_true",
                   help="continue an interrupted run, skipping recorded hosts")
    r.add_argument("--force", action="store_true",
                   help="overwrite a finished run of the same id")
    r.add_argument("--allow-unresolvable", action="store_true",
                   help="probe panel hosts that do not resolve; marks the run degraded")
    r.add_argument("--dry-run", action="store_true",
                   help="write to data/runs/dry-run and leave robots texts alone")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("resolve", help="check every panel host resolves (offline of the hosts)")
    v.add_argument("--panel", default="panel-01")
    v.set_defaults(func=cmd_resolve)

    rp = sub.add_parser("reparse", help="re-derive policy from archived bodies")
    rp.add_argument("--panel", default="panel-01")
    rp.add_argument("--dry-run", action="store_true")
    rp.set_defaults(func=cmd_reparse)

    c = sub.add_parser("cat", help="print a stored file for one host")
    c.add_argument("--panel", default="panel-01")
    c.add_argument("host")
    c.add_argument("--file", default="robots",
                   choices=["robots", "llms_txt", "ai_txt", "tdmrep"])
    c.add_argument("--run")
    c.set_defaults(func=cmd_cat)

    df = sub.add_parser("diff", help="diff one host's file between two runs")
    df.add_argument("--panel", default="panel-01")
    df.add_argument("host")
    df.add_argument("from_run")
    df.add_argument("to_run")
    df.add_argument("--file", default="robots",
                    choices=["robots", "llms_txt", "ai_txt", "tdmrep"])
    df.set_defaults(func=cmd_diff)

    cp = sub.add_parser("corpus", help="list stored files of one kind")
    cp.add_argument("--panel", default="panel-01")
    cp.add_argument("--file", default="llms_txt",
                    choices=["robots", "llms_txt", "ai_txt", "tdmrep"])
    cp.add_argument("--run")
    cp.set_defaults(func=cmd_corpus)

    t = sub.add_parser("retry", help="re-probe failed hosts in the latest run")
    t.add_argument("--panel", default="panel-01")
    t.add_argument("--pause", type=float, default=30.0)
    t.set_defaults(func=cmd_retry)

    vf = sub.add_parser("verify", help="check archived bodies against their hashes")
    vf.add_argument("--panel", default="panel-01")
    vf.set_defaults(func=cmd_verify)

    rs = sub.add_parser("reshard", help="split a run's records into per-band files")
    rs.add_argument("--panel", default="panel-01")
    rs.add_argument("--dry-run", action="store_true",
                    help="report the split without writing")
    rs.set_defaults(func=cmd_reshard)

    d = sub.add_parser("derive", help="compute findings from the latest run")
    d.add_argument("--panel", default="panel-01")
    d.set_defaults(func=cmd_derive)

    args = ap.parse_args()
    if getattr(args, "panel", None):
        globals()["PANEL_ID"] = args.panel
    for p in (BLOBS, RUNS, SITE_DATA):
        p.mkdir(parents=True, exist_ok=True)
    random.seed(0)
    args.func(args)


if __name__ == "__main__":
    main()
