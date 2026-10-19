"""Mechanical screening rules for panel hosts.

The top-1000 panel was screened by hand. That is not repeatable at 20,000
rows, so these rules stand in -- and because the hand-screened set exists,
their accuracy is measurable rather than assumed:

    uv run observatory.py validate-rules

reports agreement against every reviewed decision. Publish that number with
any panel these rules produced.

Design notes, each one paid for by an error the validation caught:

- Match on domain structure, never bare substrings. The first draft used
  `force\\.com` and deleted salesforce.com; `zoom\\.us$` deleted zoom.us.
  Patterns here match a whole registrable label or a genuine suffix.
- An apex that serves a real company site is a website even when the company
  sells advertising technology. criteo.com is a website; adnxs.com is not.
  KEEP_DOMAINS carries those, taken from the hand-screened set.
- Duplicate collapse is structural, not a pattern: group by registrable label
  and keep the highest-ranked. That alone fixed most of the misses.
"""

from __future__ import annotations

import re

RULES_VERSION = 1

# --- suffixes whose entire purpose is delivery, resolution or measurement ---
EXCLUDE_SUFFIXES = (
    # CDN and edge
    "akamai.net", "akamaiedge.net", "akadns.net", "akam.net", "akamaized.net",
    "akamaihd.net", "akamaitech.net", "akamaitechnologies.com", "edgekey.net",
    "edgesuite.net", "cloudfront.net", "fastly.net", "fastlylb.net",
    "gstatic.com", "googleusercontent.com", "googlevideo.com", "ggpht.com",
    "ytimg.com", "fbcdn.net", "cdninstagram.com", "twimg.com", "licdn.com",
    "pinimg.com", "mzstatic.com", "aaplimg.com", "cdn-apple.com",
    "media-amazon.com", "ssl-images-amazon.com", "amazonvideo.com",
    "jsdelivr.net", "unpkg.com", "bootstrapcdn.com", "alicdn.com",
    "b-cdn.net", "cdn77.org", "typekit.net", "githubusercontent.com",
    "usercontent.goog", "fbsbx.com", "scdn.co", "icloud-content.com",
    "rbxcdn.com", "ttvnw.net", "nflxso.net", "nflximg.net", "nflxvideo.net",
    "steamstatic.com", "vkuser.net", "imgsmail.ru",
    # cloud, API and device endpoints
    "amazonaws.com", "googleapis.com", "azureedge.net", "azurefd.net",
    "azurewebsites.net", "trafficmanager.net", "windows.net",
    "windowsupdate.com", "elasticbeanstalk.com", "awsglobalaccelerator.com",
    "awswaf.com", "appspot.com", "herokuapp.com", "herokudns.com",
    "netlify.app", "vercel.app", "workers.dev", "pages.dev", "run.app",
    "oraclecloud.com", "yandexcloud.net", "aliyuncs.com", "myqcloud.com",
    "tencent-cloud.net", "myhuaweicloud.com", "usgovcloudapi.net",
    "azure-devices.net", "xboxlive.com", "playstation.net", "steamserver.net",
    "whatsapp.net", "facebook.net", "sharepoint.com", "microsoftonline.com",
    "msedge.net", "msftconnecttest.com", "msftncsi.com", "msftauth.net",
    "msidentity.com", "cloudapp.net", "cloudapp.azure.com",
    # DNS, registry, certificates
    "gtld-servers.net", "root-servers.net", "nstld.com", "domaincontrol.com",
    "registrar-servers.com", "name-services.com", "worldnic.com",
    "dnsmadeeasy.com", "azure-dns.com", "yahoodns.net", "ui-dns.com",
    "apple-dns.net", "alidns.com", "alibabadns.com", "dnspod.net",
    "amazontrust.com", "digicert.com", "globalsign.com", "sectigo.com",
    "lencr.org", "pki.goog", "ocsp.com",
    # advertising, analytics and telemetry serving endpoints
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "googletagservices.com", "googletagmanager.com", "google-analytics.com",
    "2mdn.net", "adnxs.com", "adsrvr.org", "demdex.net", "omtrdc.net",
    "adobedc.net", "adobedtm.com", "scorecardresearch.com", "casalemedia.com",
    "rubiconproject.com", "pubmatic.com", "openx.net", "lijit.com",
    "rlcdn.com", "crwdcntrl.net", "agkn.com", "bidswitch.net", "bidr.io",
    "adsafeprotected.com", "quantserve.com", "imrworldwide.com",
    "online-metrix.net", "app-measurement.com", "appsflyersdk.com",
    "crashlytics.com", "nr-data.net", "clarity.ms", "go-mpulse.net",
    "amazon-adsystem.com", "smartadserver.com", "3lift.com", "360yield.com",
    "id5-sync.com", "creativecdn.com", "adform.net", "flashtalking.com",
)

# --- whole registrable labels that are never a website ---
EXCLUDE_LABELS = {
    "cdn", "cdn1", "cdn2", "static", "assets", "img", "imgs", "images",
    "media", "edge", "origin", "cache", "ns1", "ns2", "dns", "mx", "mail",
    "smtp", "mta", "api", "apis", "telemetry", "metrics", "analytics",
    "tracking", "beacon", "pixel", "ads", "adserver", "ocsp", "crl", "ntp",
}

# --- anchored patterns on the registrable label ---
# Deliberately conservative: each must be a label shape, not a substring that
# can appear inside an ordinary brand name.
LABEL_PATTERNS = (
    (re.compile(r"^(cdn|cdns)[0-9-]*$"), "CDN / asset delivery"),
    (re.compile(r"cdn$"), "CDN / asset delivery"),
    (re.compile(r"^(ns|dns)[0-9]*$"), "DNS infrastructure"),
    (re.compile(r"^(api|apis)[0-9-]*$"), "cloud / API endpoint"),
    (re.compile(r"dns$"), "DNS infrastructure"),
    (re.compile(r"^(ad|ads|adserver|adservice)[0-9-]*$"), "advertising endpoint"),
)

# --- apexes that ARE real company websites despite what they sell ---
# From the hand-screened set. An advertising vendor's own site is a website.
KEEP_DOMAINS = {
    "criteo.com", "taboola.com", "outbrain.com", "pubmatic.com", "inmobi.com",
    "applovin.com", "doubleverify.com", "media.net", "teads.tv", "branch.io",
    "adjust.com", "braze.com", "klaviyo.com", "qualtrics.com", "singular.net",
    "moloco.com", "liftoff.io", "stackadapt.com", "sharethrough.com",
    "seedtag.com", "gumgum.com", "yieldmo.com", "33across.com",
    "onetrust.com", "forter.com", "vungle.com", "appsflyer.com",
    "conviva.com", "smartadserver.com", "newrelic.com", "amplitude.com",
    "mixpanel.com", "hotjar.com", "pendo.io", "optimizely.com",
    "allaboutcookies.org", "networkadvertising.org", "aboutads.info",
    "cookiedatabase.org", "salesforce.com", "zoom.us", "gmail.com",
    "outlook.com", "live.com", "icloud.com", "odoo.com", "bitrix24.ru",
    "chinamobile.com", "cloudinary.com", "datadoghq.com", "launchdarkly.com",
    "sentry.io", "cloudflare.com", "fastly.com", "digicert.com",
}

# --- exact apexes that are not websites and match no structural rule --------
EXCLUDE_DOMAINS = {
    "example.com", "localhost", "ipv4only.arpa", "resolver.arpa",
    "3gppnetwork.org", "one.one", "ipify.org", "ip-api.com", "icanhazip.com",
    "msftconnecttest.com", "recaptcha.net", "hcaptcha.com", "cookielaw.org",
    "dns.google", "opendns.com", "publicnode.com", "cloudflare-dns.com",
    "duckdns.org", "dyndns.org", "no-ip.com", "ddnss.de", "cloudns.net",
    "expireddomains.com", "dropcatch.com", "hugedomains.com", "buydomains.com",
    "abovedomains.com", "sedo.com", "afternic.com", "parklogic.com",
    "markmonitor.com", "verisign.com", "github.io", "myshopify.com",
    "wixsite.com", "blogspot.com", "wp.com", "force.com", "appcenter.ms",
    "bit.ly", "tinyurl.com", "goo.gl", "g.co", "g.page", "forms.gle",
    "t.co", "youtu.be", "amzn.to", "wa.me", "t.me", "linktr.ee",
    "mailchi.mp", "list-manage.com", "share.google", "translate.goog",
    "withgoogle.com", "android.com",
}


def classify(host: str) -> str | None:
    """Return an exclusion reason, or None to keep.

    Order matters: the keep list wins over every structural rule, because the
    structural rules are what wrongly deleted those sites in the first place.
    """
    host = host.strip().lower()
    if host in KEEP_DOMAINS:
        return None
    if host in EXCLUDE_DOMAINS:
        return "not a content website"
    for suf in EXCLUDE_SUFFIXES:
        if host == suf or host.endswith("." + suf):
            return "delivery, resolution or measurement infrastructure"
    label = host.split(".")[0]
    if label in EXCLUDE_LABELS:
        return "infrastructure hostname"
    for pat, why in LABEL_PATTERNS:
        if pat.search(label):
            return why
    return None


def collapse_duplicates(rows: list[dict]) -> dict[str, str]:
    """host -> the higher-ranked sibling it duplicates.

    Structural rather than pattern-based: group by registrable label and keep
    the highest-ranked. Catches amazon.co.uk/.de/.fr without naming any of
    them. It over-collapses genuinely unrelated same-label hosts, which is why
    the validation reports it separately.
    """
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["host"].split(".")[0], []).append(r)
    dupes = {}
    for label, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: int(r["rank"]))
        for m in members[1:]:
            dupes[m["host"]] = members[0]["host"]
    return dupes
