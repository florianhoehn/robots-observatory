"""Directive-preserving robots.txt parser.

The stdlib's urllib.robotparser answers "may I fetch this URL" and throws the
directives away. The directives *are* our dataset, so we parse them ourselves.

Deterministic by design: no model, no heuristics beyond the documented HTML
sniff. The project's output is the diff between weekly runs, so any
non-determinism here would surface as a policy change indistinguishable from a
real one.

Bump PARSER_VERSION on any behaviour change. Stored raw robots.txt plus a
version stamp is what lets a later fix be re-derived over the whole history
instead of silently breaking comparability with week 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

PARSER_VERSION = 2

# Directives we treat as standard group members (RFC 9309 plus crawl-delay).
_GROUP_STANDARD = {"allow", "disallow", "crawl-delay"}
# Directives that are global rather than per-group.
_GLOBAL_STANDARD = {"sitemap", "host"}
# Non-standard directives worth recording wherever they appear. "license" is
# RSL's pay-per-crawl pointer; the others are AI-preference experiments.
_NOTABLE = {"license", "content-usage", "content-signal", "noai", "noimageai"}

# Markers that mean a 200 response body is a web page, not a robots file. Many
# hosts answer /robots.txt with their homepage; parsed naively that invents a
# policy for hundreds of hosts and shifts every finding in the dataset.
_HTML_MARKERS = ("<!doctype html", "<html", "<head", "<body", "<script", "<title")

# A 200 carrying one of these is a bot challenge, not a missing file. Calling
# LinkedIn's reCAPTCHA page a soft 404 would hide the finding: the host blocks
# unknown clients from reading the file robots.txt exists to be read from.
_CHALLENGE_MARKERS = (
    "recaptcha", "challengepage", "attention required", "just a moment",
    "cf-browser-verification", "cf_chl", "datadome", "perimeterx",
    "px-captcha", "hcaptcha", "verifying you are human",
)
# "enable javascript and cookies" was tried here and removed: it is Cloudflare's
# wording but also appears on cookie-consent walls, which are a separate state
# (redirected_away). Cloudflare challenges are caught by the markers above.


@dataclass
class Group:
    """One robots.txt group: consecutive user-agent lines plus their rules."""

    agents: list[str] = field(default_factory=list)  # raw casing preserved
    allow: list[str] = field(default_factory=list)
    disallow: list[str] = field(default_factory=list)
    crawl_delay: str | None = None
    notable: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Robots:
    groups: list[Group] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    global_notable: dict[str, list[str]] = field(default_factory=dict)
    unknown_fields: list[str] = field(default_factory=list)
    directive_lines: int = 0

    def agent_tokens(self) -> list[str]:
        """Every user-agent token named anywhere, raw casing, deduped in order."""
        seen: dict[str, None] = {}
        for g in self.groups:
            for a in g.agents:
                seen.setdefault(a, None)
        return list(seen)

    def group_for(self, token: str) -> Group | None:
        """The group applying to `token`.

        RFC 9309: product tokens match case-insensitively and the most specific
        group wins. We match whole tokens exactly (case-insensitively) and fall
        back to the `*` group -- no substring matching, which is non-standard and
        would make results depend on which crawler we chose to imitate.
        """
        low = token.lower()
        for g in self.groups:
            if any(a.lower() == low for a in g.agents):
                return g
        for g in self.groups:
            if any(a == "*" for a in g.agents):
                return g
        return None

    def names(self, token: str) -> bool:
        low = token.lower()
        return any(a.lower() == low for g in self.groups for a in g.agents)


def looks_like_html(body: str) -> bool:
    """True if the body is a web page rather than a robots file."""
    head = body[:2048].lstrip().lower()
    return any(m in head for m in _HTML_MARKERS)


def looks_like_challenge(body: str) -> bool:
    """True if the body is a CAPTCHA or bot-verification interstitial."""
    head = body[:4096].lower()
    return any(m in head for m in _CHALLENGE_MARKERS)


def classify(status: int | None, body: str | None, transport_error: bool,
             path_mismatch: bool = False) -> str:
    """Bucket the response.

    These states are genuinely different claims and averaging them produces a
    confident wrong number: a 404 means "no file at all", which is not the same
    as "has a file that ignores AI", which is not the same as "would not let us
    read the file". Every one of the following was found in the first ten hosts
    of a dry run:

      present         a real policy file
      absent_404      no such file
      blocked_403     refused outright
      undefined_5xx   server error; undefined, NOT permitted
      soft_404        200 carrying a web page instead of the file
      challenged      200 carrying a CAPTCHA or bot-verification page
      redirected_away 200, but at a different path than we asked for --
                      typically a cookie-consent wall, which a UK or EU
                      vantage meets far more often than a US one
      error           transport failure
    """
    if transport_error or status is None:
        return "error"
    if status in (404, 410):
        return "absent_404"
    if status in (401, 403, 451):
        return "blocked_403"
    if status >= 500:
        return "undefined_5xx"
    if status == 200:
        if body is None:
            return "error"
        if looks_like_challenge(body):
            return "challenged"
        if path_mismatch:
            return "redirected_away"
        if looks_like_html(body):
            return "soft_404"
        return "present"
    return "unexpected_status"


def parse(text: str) -> Robots:
    """Parse robots.txt, preserving directives and group structure."""
    r = Robots()
    group: Group | None = None
    # A group is a run of user-agent lines followed by rules. Another
    # user-agent line *after* a rule line starts a new group.
    accepting_agents = False

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            r.unknown_fields.append(line[:80])
            continue

        name, _, value = line.partition(":")
        key = name.strip().lower()
        val = value.strip()
        r.directive_lines += 1

        if key == "user-agent":
            if group is None or not accepting_agents:
                group = Group()
                r.groups.append(group)
                accepting_agents = True
            group.agents.append(val)
            continue

        if key in _GROUP_STANDARD:
            accepting_agents = False
            if group is None:
                # Rules before any user-agent line: malformed but common. Treat
                # them as a wildcard group rather than discarding them.
                group = Group(agents=["*"])
                r.groups.append(group)
            if key == "allow":
                group.allow.append(val)
            elif key == "disallow":
                group.disallow.append(val)
            else:
                group.crawl_delay = val
            continue

        if key in _GLOBAL_STANDARD:
            accepting_agents = False
            if key == "sitemap":
                r.sitemaps.append(val)
            continue

        if key in _NOTABLE:
            accepting_agents = False
            target = group.notable if group is not None else r.global_notable
            target.setdefault(key, []).append(val)
            continue

        accepting_agents = False
        r.unknown_fields.append(key[:40])

    return r


def _match_len(pattern: str, path: str) -> int:
    """Length of `pattern` if it matches `path`, else -1.

    Supports the two widely-honoured wildcards: `*` for any run of characters
    and `$` to anchor the end. Specificity is the pattern's length, per RFC 9309.
    """
    if pattern == "":
        return -1  # an empty Disallow is not a rule at all
    if "*" not in pattern and "$" not in pattern:
        return len(pattern) if path.startswith(pattern) else -1

    anchored = pattern.endswith("$")
    pat = pattern[:-1] if anchored else pattern
    parts = pat.split("*")

    pos = 0
    if not path.startswith(parts[0]):
        return -1
    pos = len(parts[0])
    for seg in parts[1:]:
        if seg == "":
            continue
        idx = path.find(seg, pos)
        if idx == -1:
            return -1
        pos = idx + len(seg)
    if anchored and pos != len(path):
        return -1
    return len(pattern)


def path_disallowed(group: Group | None, path: str = "/") -> bool:
    """Is `path` disallowed for this group?

    Longest matching rule wins; on equal length Allow wins (the least
    restrictive reading, which is what the major crawlers implement). An empty
    `Disallow:` value means allow-all for the group and must not be read as a
    block -- inverting that single rule flips the project's headline number.
    """
    if group is None:
        return False
    best_dis = max((_match_len(p, path) for p in group.disallow), default=-1)
    best_allow = max((_match_len(p, path) for p in group.allow), default=-1)
    if best_dis < 0:
        return False
    return best_dis > best_allow


def summarise(text: str, watchlist: list[str], us: str) -> dict:
    """The parsed-policy block of a record.

    `us` is our own user-agent product token: `would_disallow_us` is computed
    from the declared policy and never acted on, which recovers a predicted
    access signal without fetching anything we were asked not to fetch.
    """
    r = parse(text)
    wildcard = next((g for g in r.groups if any(a == "*" for a in g.agents)), None)

    named = [t for t in watchlist if r.names(t)]
    return {
        "all_agent_tokens_seen": r.agent_tokens(),
        "ai_tokens_named": named,
        "ai_tokens_root_disallowed": [
            t for t in named if path_disallowed(r.group_for(t))
        ],
        "wildcard_root_disallowed": path_disallowed(wildcard),
        "crawl_delay": wildcard.crawl_delay if wildcard else None,
        "sitemaps": len(r.sitemaps),
        "rsl_license_refs": sorted(
            {v for g in r.groups for v in g.notable.get("license", [])}
            | set(r.global_notable.get("license", []))
        ),
        "nonstandard_directives": sorted(
            {k for g in r.groups for k in g.notable} | set(r.global_notable)
        ),
        "unknown_fields": sorted(set(r.unknown_fields))[:10],
        "groups": len(r.groups),
        "directive_lines": r.directive_lines,
        "would_disallow_us": path_disallowed(r.group_for(us)),
    }
