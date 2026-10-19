#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx==0.28.1"]
# ///
"""Fetcher and conduct-limit tests, against a local server.

The politeness limits in observatory.py are the ethical argument in CONDUCT.md
expressed as code, so they should be verifiable rather than asserted. This
checks them against a throwaway HTTP server on localhost -- it makes no
external requests, which matters because the charter says nothing touches the
network until the contact page is live.

Takes about 25 seconds: the per-host gap is real and is not mocked away.

    uv run tests/test_fetch.py
"""

import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["OBSERVATORY_SCHEME"] = "http"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import observatory as obs  # noqa: E402

ROBOTS = b"User-agent: *\nDisallow: /admin/\n\nUser-agent: GPTBot\nDisallow: /\n"
OPTOUT = (Path(__file__).parent / "fixtures" / "optout.txt").read_bytes()

inflight = 0
max_inflight = 0
hits: list[tuple[float, str, str]] = []
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # silence
        pass

    def do_GET(self):  # noqa: N802
        global inflight, max_inflight
        with lock:
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            hits.append((time.monotonic(), self.path,
                         self.headers.get("User-Agent", "")))
        try:
            time.sleep(0.05)
            if self.path == "/optout-robots":
                self._send(200, OPTOUT, "text/plain")
            elif self.path == "/robots.txt":
                self._send(200, ROBOTS, "text/plain", extra={
                    "Age": "0", "X-Cache": "MISS", "Server": "testd",
                    "Set-Cookie": "sid=secret-value-must-not-be-stored",
                    "X-Should-Not-Be-Stored": "leak",
                })
            elif self.path == "/llms.txt":
                self._send(200, b"# llms\n", "text/plain")
            elif self.path == "/big":
                self._send(200, b"x" * (obs.READ_CAP_BYTES + 50_000), "text/plain")
            elif self.path == "/hop1":
                self._redirect("/hop2")
            elif self.path == "/hop2":
                self._redirect("/robots.txt")
            elif self.path == "/loop":
                self._redirect("/loop")
            elif self.path == "/softy":
                self._send(200, b"<!DOCTYPE html><html><body>nope</body></html>",
                           "text/html")
            else:
                self._send(404, b"not found", "text/plain")
        finally:
            with lock:
                inflight -= 1

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()


failures: list[str] = []


def check(name, got, want):
    if got != want:
        failures.append(f"{name}\n    got  {got!r}\n    want {want!r}")
    else:
        print(f"  ok  {name}")


srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"127.0.0.1:{port}"
url = f"http://{base}"
ident = obs.IDENTITIES["honest_bot"]

# --- basic fetch ----------------------------------------------------------
r = obs.fetch(f"{url}/robots.txt", ident)
check("status", r["status"], 200)
check("bytes", r["bytes"], len(ROBOTS))
check("sha256 recorded", bool(r["sha256"]), True)
check("final_host", r["final_host"], "127.0.0.1")
check("final_path recorded", r["final_path"], "/robots.txt")
check("no path mismatch on a straight fetch", r["path_mismatch"], False)
check("body returned", r["_body"], ROBOTS.decode())
check("no error", r["error"], None)

# --- header allowlist: only listed headers, cookie value never stored -----
check("age stored", r["headers"].get("age"), "0")
check("x-cache stored", r["headers"].get("x-cache"), "MISS")
check("set-cookie reduced to presence", r["headers"].get("set-cookie"), "<present>")
check("cookie VALUE still redacted despite archiving everything",
      any("secret-value" in str(v) for v in r["headers"].values()), False)
check("and recorded as present", r["headers"].get("set-cookie"), "<present>")
# Every header is archived now: an allowlist decides in advance which future
# questions are answerable, and that cannot be revisited without crawling again.
check("arbitrary headers are archived, not dropped",
      r["headers"].get("x-should-not-be-stored"), "leak")
check("header names are normalised to lowercase",
      all(k == k.lower() for k in r["headers"]), True)

# --- 404 and soft 404 -----------------------------------------------------
check("404 status", obs.fetch(f"{url}/nope", ident)["status"], 404)
soft = obs.fetch(f"{url}/softy", ident)
check("soft 404 body is html",
      obs.robots_parse.classify(soft["status"], soft["_body"], False), "soft_404")

# --- read cap and truncation flag -----------------------------------------
big = obs.fetch(f"{url}/big", ident)
check("read capped at the limit", big["bytes"], obs.READ_CAP_BYTES)
check("truncation flagged", big["truncated"], True)

# --- redirects: followed, counted, and bounded ----------------------------
hop = obs.fetch(f"{url}/hop1", ident)
check("redirect chain followed", hop["status"], 200)
check("redirect count recorded", hop["redirects"], 2)
check("redirect to a different path is flagged", hop["path_mismatch"], True)
check("and classified as redirected_away, not present",
      obs.robots_parse.classify(hop["status"], hop["_body"], False,
                                hop["path_mismatch"]), "redirected_away")
loop = obs.fetch(f"{url}/loop", ident)
check("redirect limit stops a loop (recorded as an error, not a hang)",
      loop["status"] is None and "Redirect" in (loop["error"] or ""), True)

# --- identities: no request is ever anonymous -----------------------------
# The invariant is attribution, not a specific header. `From:` is an email
# mailbox per RFC 9110, so it ships only when one is configured; the UA's
# `(+url)` convention and X-Observatory-Contact carry attribution regardless.
for name, hdrs in obs.IDENTITIES.items():
    check(f"{name}: contact header present", bool(hdrs.get("X-Observatory-Contact")), True)
    check(f"{name}: user-agent non-empty", bool(hdrs.get("User-Agent")), True)
check("browser control is not anonymous",
      "Mozilla" in obs.IDENTITIES["browser_control"]["User-Agent"]
      and bool(obs.IDENTITIES["browser_control"]["X-Observatory-Contact"]), True)
check("no From: header when no mailbox is configured",
      any("From" in h for h in obs.IDENTITIES.values()), bool(obs.CONTACT_EMAIL))
check("a URL is never accepted as a From: mailbox",
      "@" in obs.CONTACT_EMAIL and "/" not in obs.CONTACT_EMAIL
      if obs.CONTACT_EMAIL else True, True)
# The server echoes what actually arrived on the wire.
sent = obs.fetch(f"{url}/robots.txt", obs.IDENTITIES["honest_bot"])
check("fetch succeeded for the header check", sent["status"], 200)
check("UA on the wire names the project and links here",
      hits[-1][2].startswith("RobotsObservatory/1.0 (+"), True)

# --- conduct: per-host gap ------------------------------------------------
with lock:
    hits.clear()
row = {"host": base, "rank_at_freeze": "1", "sector": "reference", "adult": "false"}
t0 = time.monotonic()
rec = obs.probe_host(row, ["GPTBot", "CCBot"], "test", "test", write_text=False)
elapsed = time.monotonic() - t0

check("five requests per host", len(hits), 5)
check("per-host gap honoured (>= 4 x 1.5s)", elapsed >= 4 * obs.HOST_GAP_S, True)
gaps = [round(hits[i + 1][0] - hits[i][0], 2) for i in range(len(hits) - 1)]
check("every inter-request gap >= 1.5s", all(g >= obs.HOST_GAP_S - 0.05 for g in gaps), True)
print(f"      gaps: {gaps}")

# --- record shape ---------------------------------------------------------
check("two robots entries", len(rec["robots"]), 2)
check("identical files across identities: no policy variance",
      rec["policy_varies_by_identity"], False)
check("and no differential access", rec["access_varies_by_identity"], False)
check("raw hash diff also false", rec["hash_differs_by_identity"], False)
check("file_state present", rec["policy"]["file_state"], "present")
check("GPTBot named", rec["policy"]["ai_tokens_named"], ["GPTBot"])
check("GPTBot root-disallowed", rec["policy"]["ai_tokens_root_disallowed"], ["GPTBot"])
check("would_disallow_us false under the wildcard group",
      rec["policy"]["would_disallow_us"], False)
check("llms.txt found", rec["wellknown"]["llms_txt"]["status"], 200)
check("ai.txt absent", rec["wellknown"]["ai_txt"]["status"], 404)
check("tdmrep absent", rec["wellknown"]["tdmrep"]["status"], 404)
check("sector carried through", rec["sector"], "reference")
check("parser_version stamped", rec["parser_version"], obs.robots_parse.PARSER_VERSION)
check("no errors", rec["errors"], [])

# --- opt-out through the standard robots.txt mechanism --------------------
# A host that names our token and disallows it is read once and nothing more.
check("our token recognised as disallowed",
      obs.robots_parse.summarise(OPTOUT.decode(), [], obs.US_TOKEN)["would_disallow_us"],
      True)
check("a wildcard-only rule does not trigger it",
      obs.robots_parse.summarise(ROBOTS.decode(), [], obs.US_TOKEN)["would_disallow_us"],
      False)


class OptoutHandler(Handler):
    def do_GET(self):  # noqa: N802
        with lock:
            hits.append((time.monotonic(), self.path, ""))
        if self.path == "/robots.txt":
            self._send(200, OPTOUT, "text/plain")
        else:
            self._send(200, b"should never be fetched\n", "text/plain")


srv2 = ThreadingHTTPServer(("127.0.0.1", 0), OptoutHandler)
threading.Thread(target=srv2.serve_forever, daemon=True).start()
base2 = f"127.0.0.1:{srv2.server_address[1]}"

with lock:
    hits.clear()
rec2 = obs.probe_host(
    {"host": base2, "rank_at_freeze": "9", "sector": "other", "adult": "false"},
    [], "test", "test", write_text=False)
srv2.shutdown()

check("opted-out host flagged", rec2["opted_out_via_robots"], True)
check("and not misreported as a blanket wildcard block",
      rec2["wildcard_blocks_us"], False)
check("only robots.txt was requested, twice", len(hits), 2)
check("no well-known path was touched",
      [h[1] for h in hits], ["/robots.txt", "/robots.txt"])
check("well-known entries marked skipped, not 404",
      {k: v.get("skipped") for k, v in rec2["wellknown"].items()},
      {k: "named_our_token" for k in obs.WELLKNOWN})
check("its declared policy is still recorded as a finding",
      rec2["policy"]["file_state"], "present")

with lock:
    hits.clear()
rec3 = obs.probe_host(
    {"host": base, "rank_at_freeze": "1", "sector": "other", "adult": "false"},
    [], "test", "test", write_text=False)
check("permissive host still gets all five requests", len(hits), 5)
check("permissive host not flagged", rec3["opted_out_via_robots"], False)

# --- conduct: concurrency ceiling -----------------------------------------
with lock:
    hits.clear()
    globals()["max_inflight"] = 0
rows = [{"host": base, "rank_at_freeze": str(i), "sector": "other", "adult": "false"}
        for i in range(12)]
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

with ThreadPoolExecutor(obs.CONCURRENT_HOSTS) as pool:
    list(pool.map(lambda r: obs.probe_host(r, [], "test", "test", False), rows))
check(f"concurrency never exceeded {obs.CONCURRENT_HOSTS} hosts",
      max_inflight <= obs.CONCURRENT_HOSTS, True)
print(f"      peak in-flight: {max_inflight}")

srv.shutdown()
print()
if failures:
    print(f"{len(failures)} FAILED\n")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("all passed")
