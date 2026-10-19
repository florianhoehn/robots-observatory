#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Parser tests, one per entry in the spec's correctness-traps list.

Each of these silently corrupts a published number, and most look like nothing
in casual testing because they only bite at the scale of a real panel. Run with
`uv run tests/test_robots.py` -- no pytest, no dependencies.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import robots_parse as rp  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
WATCH = ["GPTBot", "CCBot", "anthropic-ai", "Bytespider", "ClaudeBot"]
US = "ObservatoryBot"

failures: list[str] = []


def check(name: str, got, want) -> None:
    if got != want:
        failures.append(f"{name}\n    got  {got!r}\n    want {want!r}")
    else:
        print(f"  ok  {name}")


def fixture(n: str) -> str:
    return (FIX / n).read_text(encoding="utf-8")


# --- standard shape -------------------------------------------------------
r = rp.parse(fixture("plain.txt"))
check("plain: group count", len(r.groups), 3)
check("plain: tokens seen", r.agent_tokens(),
      ["*", "GPTBot", "CCBot", "anthropic-ai"])
check("plain: sitemaps", r.sitemaps, ["https://example.com/sitemap.xml"])
check("plain: shared group reaches both agents",
      (rp.path_disallowed(r.group_for("CCBot")),
       rp.path_disallowed(r.group_for("anthropic-ai"))), (True, True))
check("plain: wildcard allows root (only /admin/ blocked)",
      rp.path_disallowed(r.group_for("*")), False)
check("plain: longest match wins under /admin/",
      (rp.path_disallowed(r.group_for("*"), "/admin/x"),
       rp.path_disallowed(r.group_for("*"), "/admin/public/x")), (True, False))
check("plain: crawl-delay kept", r.group_for("*").crawl_delay, "10")

# --- trap: empty Disallow means allow-all ---------------------------------
r = rp.parse(fixture("empty_disallow.txt"))
check("empty Disallow is not a block", rp.path_disallowed(r.group_for("*")), False)

# --- trap: group precedence, wildcard must not leak -----------------------
r = rp.parse(fixture("precedence.txt"))
check("precedence: wildcard blocks root", rp.path_disallowed(r.group_for("*")), True)
check("precedence: specific group wins for GPTBot",
      rp.path_disallowed(r.group_for("GPTBot")), False)
check("precedence: GPTBot still blocked under /private/",
      rp.path_disallowed(r.group_for("GPTBot"), "/private/x"), True)
check("precedence: unlisted agent falls back to wildcard",
      rp.path_disallowed(r.group_for("ClaudeBot")), True)

# --- trap: soft 404 -------------------------------------------------------
html = fixture("soft404.html")
check("soft 404 detected", rp.looks_like_html(html), True)
check("soft 404 classified", rp.classify(200, html, False), "soft_404")
check("real robots not flagged as html", rp.looks_like_html(fixture("plain.txt")), False)

# --- trap: the five file states are distinct ------------------------------
check("state 200", rp.classify(200, fixture("plain.txt"), False), "present")
check("state 404", rp.classify(404, "", False), "absent_404")
check("state 403", rp.classify(403, "", False), "blocked_403")
check("state 503 is undefined, not permitted",
      rp.classify(503, "", False), "undefined_5xx")
check("state transport error", rp.classify(None, None, True), "error")

# --- trap: case-insensitive matching, raw casing preserved ----------------
r = rp.parse(fixture("messy.txt"))
check("lowercase token matches", r.names("GPTBot"), True)
check("raw casing preserved", "gptbot" in r.agent_tokens(), True)
check("orphan rules before any user-agent become a wildcard group",
      rp.path_disallowed(r.group_for("*"), "/orphan/x"), True)
check("RSL License captured",
      rp.summarise(fixture("messy.txt"), WATCH, US)["rsl_license_refs"],
      ["https://example.com/licence.xml"])
check("non-standard directives captured",
      rp.summarise(fixture("messy.txt"), WATCH, US)["nonstandard_directives"],
      ["content-usage", "license"])
check("unknown fields recorded, not silently dropped",
      "unknown-directive" in rp.summarise(fixture("messy.txt"), WATCH, US)["unknown_fields"],
      True)

# --- wildcards and end anchors --------------------------------------------
r = rp.parse(fixture("wildcards.txt"))
g = r.group_for("*")
check("$ anchor matches", rp.path_disallowed(g, "/docs/a.pdf"), True)
check("$ anchor respects the end", rp.path_disallowed(g, "/docs/a.pdf?x=1"), False)
check("mid-pattern * matches", rp.path_disallowed(g, "/tmp/abc/cache"), True)
check("mid-pattern * does not over-match", rp.path_disallowed(g, "/tmp/abc/other"), False)
check("Disallow: /* blocks root", rp.path_disallowed(r.group_for("Bytespider")), True)

# --- large file -----------------------------------------------------------
big = fixture("large.txt")
check("large file parses", len(big) > 50_000 and rp.parse(big).directive_lines, 2)

# --- the summarise contract -----------------------------------------------
s = rp.summarise(fixture("plain.txt"), WATCH, US)
check("summarise: named", s["ai_tokens_named"], ["GPTBot", "CCBot", "anthropic-ai"])
check("summarise: root-disallowed", s["ai_tokens_root_disallowed"],
      ["GPTBot", "CCBot", "anthropic-ai"])
check("summarise: would_disallow_us follows the wildcard group",
      s["would_disallow_us"], False)
check("summarise: watchlist order is stable, not file order",
      rp.summarise(fixture("plain.txt"), ["CCBot", "GPTBot"], US)["ai_tokens_named"],
      ["CCBot", "GPTBot"])

print()
if failures:
    print(f"{len(failures)} FAILED\n")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print(f"all passed (parser_version={rp.PARSER_VERSION})")
