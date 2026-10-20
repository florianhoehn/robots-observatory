# origin-root

The machine-readable policy files for `florianhoehn.github.io`, written out
exactly as they must appear at the origin root.

This project measures whether websites publish machine-readable policy, and
published none of its own. These are the files we would hold up as the good
case: radically open, explicitly welcoming to AI agents, and navigable by an
agent that is actually trying to find and use the dataset.

| File | Convention | What it says |
|---|---|---|
| `robots.txt` | RFC 9309 + Content Signals | allow everything, for everyone; `search=yes, ai-input=yes, ai-train=yes` |
| `llms.txt` | [llmstxt.org](https://llmstxt.org/) | a guide for AI readers: findings, the ten lenses, every data file, how to stop the crawler |
| `.well-known/security.txt` | RFC 9116 | contact and expiry, no personal mailbox |
| `.well-known/tdmrep.json` | W3C TDMRep | `"tdm-reservation": 0`, an explicit grant, not a reservation |
| `humans.txt` | [humanstxt.org](https://humanstxt.org/) | who made it, and what that costs in honesty |

**This README is not part of the deployment.** Copy the five files, leave this
one here.

## Why these are not under `docs/`

`docs/` is served at `florianhoehn.github.io/robots-observatory/`, a subpath.
Four of these five conventions are defined per *origin*, not per path:

- **robots.txt** governs an origin. A crawler fetches `/robots.txt` from the
  host root and nowhere else (RFC 9309 §2.3). A file at
  `/robots-observatory/robots.txt` is read by nothing.
- **`/.well-known/`** exists only at the origin root, by definition
  (RFC 8615). `security.txt` MUST be at `/.well-known/security.txt` for a
  web service (RFC 9116 §3); `tdmrep.json` MUST be in the origin's
  `/.well-known/` (TDMRep §6.1).
- **humans.txt** is a root convention by custom.

Put them at a subpath and they become decoration: files that look like
compliance and are read by no client. That is the exact failure mode this
project documents in other people's sites, so it is not one we are going to
ship.

**llms.txt is the single exception.** The v2 proposal explicitly allows an
`llms.txt` at any subpath, covering the pages beneath it, and names a GitHub
Pages project site as exactly the case a single root location cannot serve,
which is its stated reason for not being a `/.well-known/` URI at all. So
`docs/llms.txt`, served at
`florianhoehn.github.io/robots-observatory/llms.txt`, would be
spec-valid and would be found. It is not written here because the brief for
this directory was the origin root and `docs/` was out of scope. If you want
both, the root copy is the one to keep authoritative and the project-path
copy can be a verbatim duplicate.

## Exact placement

The origin root is served by a different repository: `florianhoehn.github.io`
(Jekyll, branch `master`, source `/`). Nothing in *this* repo can affect it.
Target layout:

    florianhoehn.github.io/        (branch master, Pages source /)
    |- _config.yml                 <- needs one line added, see below
    |- index.md                    (existing, untouched)
    |- robots.txt
    |- llms.txt
    |- humans.txt
    `- .well-known/
       |- security.txt
       `- tdmrep.json

From a clone of that repo:

    cp ~/Projects/robots-observatory/origin-root/{robots.txt,llms.txt,humans.txt} .
    mkdir -p .well-known
    cp ~/Projects/robots-observatory/origin-root/.well-known/* .well-known/

Do not add YAML front matter to any of them. Without front matter Jekyll
copies a file through byte for byte, which is what we want: no templating, no
BOM, no surprises.

## The Jekyll trap: `.well-known/` will silently not deploy

**Jekyll excludes every file and directory whose name begins with a dot.**
`.well-known/` is one. Commit it, push it, watch the build go green, and
`/.well-known/security.txt` returns 404, with nothing anywhere saying why.

This repo has already been bitten by the same class of bug from the other
direction: `docs/.nojekyll` must exist or GitHub Pages runs Jekyll over the
published site and drops files. The runbook lists it as a symptom to check.
Same trap, opposite sign.

The fix is one line in `_config.yml` at the root of `florianhoehn.github.io`:

```yaml
include: ['.well-known']
```

If `_config.yml` already has an `include:` key, add `'.well-known'` to the
existing list instead of writing a second key: Jekyll's YAML parser keeps the
last of two duplicate keys and drops the first, so a second `include:` would
quietly delete whatever the first one was for.

Do **not** reach for `.nojekyll` here. It would disable Jekyll for the whole
origin and `index.md` would stop being rendered into HTML; the existing page
at `florianhoehn.github.io/` would start serving raw Markdown. `include:` is
the narrow fix.

`robots.txt`, `llms.txt` and `humans.txt` need nothing: they are ordinary
static files at the root and Jekyll copies them as they are.

## Verify after deploying

Wait for the Pages build to finish. `errored` is not the same as slow:

    gh api repos/florianhoehn/florianhoehn.github.io/pages/builds/latest --jq .status

Then, all five should print `200`:

    for p in /robots.txt /llms.txt /humans.txt \
             /.well-known/security.txt /.well-known/tdmrep.json; do
      printf '%s  %s\n' \
        "$(curl -s -o /dev/null -w '%{http_code}' "https://florianhoehn.github.io$p")" "$p"
    done

The one most likely to fail is `/.well-known/security.txt`. A 404 there means
the `include:` line is missing or malformed, not that the file is missing
from the repo.

These five paths are also the only URLs these files reference that do not
resolve before deployment. Every other link in them was checked and returns
200. The files cross-reference each other (`robots.txt` points at
`/llms.txt`, `llms.txt` points at all three of the others), so until this
deploy lands those pointers are the only broken links on the origin. That is
the whole reason the loop above exists.

Content types, which GitHub Pages sets from the extension:

    curl -sI https://florianhoehn.github.io/robots.txt | grep -i content-type
    # text/plain; charset=utf-8
    curl -sI https://florianhoehn.github.io/.well-known/tdmrep.json | grep -i content-type
    # application/json

Then check the two things our own data says sites get wrong:

    # no byte-order mark, and nothing empty
    curl -s https://florianhoehn.github.io/robots.txt | head -c 3 | od -An -tx1
    # must NOT be  ef bb bf

    # the JSON path must parse as JSON
    curl -s https://florianhoehn.github.io/.well-known/tdmrep.json | python3 -m json.tool

And the self-test worth having, from a checkout of this repo: our own
parser, over our own robots.txt:

    python3 -c "
    import robots_parse as R, urllib.request
    t = urllib.request.urlopen('https://florianhoehn.github.io/robots.txt').read().decode()
    s = R.summarise(t, [l.strip() for l in open('agents.txt')
                        if l.strip() and not l.startswith('#')], 'RobotsObservatory')
    print({k: s[k] for k in ('wildcard_root_disallowed', 'content_signal',
                             'has_legal_text', 'starts_with_bom', 'crawl_delay')})
    "

Expected: `wildcard_root_disallowed` False, one `content_signal` value,
`has_legal_text` False, `starts_with_bom` False, `crawl_delay` None.

## Decisions you may want to revisit

- **No `Sitemap:` line.** This origin has no sitemap; verified,
  `/sitemap.xml` 404s today. Add the line only if one starts existing.
- **No crawler is named in `robots.txt`.** A named token grants nothing the
  `User-agent: *` group does not already grant, and every named token is a
  rule with an expiry date: 1,098 panel sites carry one that can never
  fire. Our own derivation would file a named-and-not-blocked list under
  support (`names_ai_blocks_none`), which is a reason to want one and a bad
  reason to write one: it would mean writing a file to flatter our own
  classifier. If you do want an explicit named welcome, the honest form is a
  second group listing only tokens the project currently tracks in
  `agents.txt` with `Allow: /`, and dropping the handful in there that are
  already retired (`anthropic-ai`, `cohere-ai`, `omgili`).
- **`Expires: 2027-10-20T00:00:00Z`** in `security.txt` is a real deadline.
  RFC 9116 wants it under a year out, so it cannot simply be set further.
  Put a reminder in a calendar. Nothing in this project checks anyone's
  expiry date, which is a good reason not to assume someone will notice
  ours going stale.
- **`robots.txt` at the origin root governs the whole origin**, including
  any other project page you publish under `florianhoehn.github.io`. Allowing
  everything here allows everything there too. That is the intent today;
  it is worth remembering the day it is not.
- **`tdmrep.json` carries no `tdm-policy`.** With `tdm-reservation: 0`
  nothing is reserved, so there is no licence to point at. The file came out
  byte-for-byte identical to tiket.com's, same sha256
  `b61f4f76...33024a64`. That is the only granting TDM reservation in the
  16,314-site panel, and the reason writing a second one makes a point.
