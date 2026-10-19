# Vantage-dependent exclusions

17 hosts are excluded from `panel-01` not because of what they are, but because
the measurement vantage cannot resolve them. **This is a property of the
measuring machine, not of those hosts**, and anyone reproducing the panel from a
different vantage should expect to find them resolvable.

They are excluded rather than probed because a host the resolver refuses to look
up records as unreachable, and that reads as a finding about the host. A
documented absence is honest; a fabricated unreachability is not.

## DNS-level ad/tracker filtering on the local network (14)

The measurement vantage is a residential machine behind a network that filters
advertising and tracking domains. These are vendor websites, in the panel
because their apex serves a real company site:

appsflyer.com, branch.io, criteo.com, disqus.com, gumgum.com, inmobi.com,
issuu.com, liftoff.io, moloco.com, outbrain.com, pubmatic.com, stackadapt.com,
taboola.com, yieldmo.com

**This is the largest known limitation of the dataset.** Advertising technology
is exactly the category most likely to have an opinion about automated access,
and it is the category this vantage cannot see. Any finding about commerce or
about vendor behaviour is reported over a panel with this hole in it.

## Not resolvable from this vantage (2)

beian.gov.cn, miit.gov.cn — Chinese government hosts, unresolvable from a UK
residential connection.

## Genuinely dead (1)

keenetic.io — NXDOMAIN, not filtering. Would be excluded from any vantage.

---

Re-check at any time:

    uv run observatory.py resolve

It resolves every panel host and reports any that fail. `observatory.py run`
does the same thing before probing anything and refuses to start if any panel
host is unresolvable, so this artefact cannot quietly grow.
