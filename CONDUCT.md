# Conduct

## What this project requests

Per host, per weekly run, exactly five requests:

| Path | Count | Why |
|---|---|---|
| `/robots.txt` | 2 | once per requester identity, to see whether declared policy varies by who asks |
| `/llms.txt` | 1 | machine-readable usage terms |
| `/ai.txt` | 1 | training opt-out convention |
| `/.well-known/tdmrep.json` | 1 | TDM Reservation Protocol |

**Five requests per host per week. 260 per year.** Spread over about nine
seconds, six hosts at a time, with 1.5 seconds between requests to any one
host. That is less traffic than a single human visitor loading a single page.

## The central claim

**No request this project makes is one a host asked bots not to make.**
`robots.txt` and the well-known paths exist precisely to be read by machines.
We never fetch a page, never touch anything behind authentication, never solve
a CAPTCHA, never circumvent a paywall, and never submit a form.

This claim is falsifiable from two readable source files: `observatory.py`
builds exactly the five URLs listed above and no others. The politeness limits
are constants at the top of that file, recorded in every run's metadata, and
verified by `tests/test_fetch.py` against a local server.

If enforcement probing is ever added — one homepage GET per identity, to learn
whether declared policy is actually applied — **this argument changes** and this
document must be rewritten before that code ships. It is deliberately absent.

## What we store

Status codes, timings, a header allowlist, content hashes, and the full text of
`robots.txt` (which is the subject of the study). Page bodies are never
retained. `Set-Cookie` is recorded as present or absent, never by value.

## Identification

Every request, in both identities, carries a `User-Agent` and an
`X-Observatory-Contact` header pointing at `https://florianhoehn.github.io/robots-observatory/bot/`. The browser
control identity exists to show what a default scraper sees — it still carries
full contact details, so **no probe this project makes is ever anonymous**.
A `From:` header is sent as well whenever a contact mailbox is configured.

We never impersonate another operator. No `GPTBot`, no `ClaudeBot`, no
`CCBot`, no third party's token, ever.

## Opt-out

**The standard mechanism works. Put this in your `robots.txt`:**

```
User-agent: RobotsObservatory
Disallow: /
```

We read `robots.txt` once — RFC 9309 exempts that file from `robots.txt` rules,
which is the only way any crawler can learn it has been told to stop — and then
fetch nothing else from your host. No well-known paths, no further requests,
this run or any future one. No custom directive to learn, nothing to email, and
nothing for us to approve.

Your declared policy still appears in the dataset, because it is published in a
public file and is the thing this project measures. If you want the host removed
from the **publication** as well, see below.

You can also [open an issue](https://github.com/florianhoehn/robots-observatory/issues/new) to be added to `optout.yaml`, which skips your host
before any request is made. Honoured within 7 days. We will not ask you to
justify it.

## Dataset delisting

Separate from the above. The published dataset names roughly 660 hosts and will
remain online indefinitely. To have a host's rows removed from the
**publication** — not just excluded from future runs — [open an issue](https://github.com/florianhoehn/robots-observatory/issues/new).

## The vantage is part of the method

Runs come from a residential UK connection. Two consequences are recorded in
every run's metadata rather than left for a reader to discover:

- The local network filters advertising and tracking domains, so 17 hosts are
  excluded from the panel with published reasons. See
  `panel/VANTAGE-EXCLUSIONS.md`.
- A UK or EU vantage meets cookie-consent interstitials that a US one often
  does not. These are recorded as `redirected_away` rather than counted as a
  file that exists.

## No model in the measurement path

Fetch, parse and count are deterministic. The product of this project is the
difference between weekly runs, so non-determinism in parsing would appear as
policy change and be indistinguishable from the real thing. No LLM is used to
collect, parse or classify anything in `data/`.
