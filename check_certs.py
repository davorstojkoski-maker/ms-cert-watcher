#!/usr/bin/env python3
"""
Microsoft Certification & Voucher Watcher — v2.1
=================================================
Pulls from four official Microsoft sources every week and produces a
single GitHub issue listing everything new that's free or discounted.

Sources
-------
1. Learn Catalog API      — new certifications / exams (any level)
2. Skills Hub Blog RSS    — beta exam announcements (80% off codes)
3. Virtual Training Days  — new events with 50% exam discount
4. Learn Deals page       — official active promos / sweepstakes

State is stored in data/snapshot.json (committed back by the workflow).
On first run only a baseline is built; no issue is opened.
"""

import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

HEADERS = {"User-Agent": "ms-cert-watcher/2.1 (github-actions)"}
SNAPSHOT_FILE = "data/snapshot.json"
ISSUE_BODY_FILE = "issue_body.md"

CATALOG_URL = (
    "https://learn.microsoft.com/api/catalog/?type=certifications,exams&locale=en-us"
)
# Skills Hub Blog RSS — plugins/custom endpoint format
SKILLS_HUB_RSS = (
    "https://techcommunity.microsoft.com/plugins/custom/microsoft/o365/custom-blog-rss"
    "?board=skills-hub-blog"
)
# VTD events — events.microsoft.com search API
VTD_URL = (
    "https://events.microsoft.com/api/v1/events/search?"
    + urlencode(
        {
            "scenario": "mvtd",
            "language": "English",
            "clientTimeZone": "UTC",
            "page": 1,
            "pageSize": 50,
        }
    )
)
VTD_PAGE = "https://www.microsoft.com/en-us/events/category/microsoft-virtual-training-days"
DEALS_URL = "https://learn.microsoft.com/en-us/credentials/certifications/deals"

# Keywords that flag a Skills Hub post as a beta-exam / discount announcement
BETA_KEYWORDS = ["beta", "80%", "discount code", "voucher", "free exam", "exam offer"]
FREE_HINT_WORDS = ["fundamentals"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fetch(url, *, accept=None):
    req = urllib.request.Request(url, headers=dict(HEADERS))
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_json(url):
    return json.loads(fetch(url, accept="application/json"))


def fetch_text(url):
    return fetch(url).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Source 1 — Learn Catalog API
# ---------------------------------------------------------------------------


def get_catalog_items():
    try:
        data = fetch_json(CATALOG_URL)
    except Exception as e:
        print(f"[catalog] fetch failed: {e}", file=sys.stderr)
        return {}

    out = {}
    for kind, key in [("Certification", "certifications"),
                      ("Certification", "mergedCertifications"),
                      ("Exam", "exams")]:
        for item in data.get(key) or []:
            uid = item.get("uid") or item.get("title", "")
            normalized = {
                "id": uid,
                "title": item.get("title", "(untitled)"),
                "kind": kind,
                "level": ((item.get("levels") or [""])[0]),
                "products": item.get("products") or [],
                "url": item.get("url") or "https://learn.microsoft.com/credentials/",
                "source": "catalog",
            }
            if is_deprecated(normalized):
                continue
            out[uid] = normalized
    print(f"[catalog] {len(out)} items fetched.")
    return out


def is_fundamentals(item):
    t = item.get("title", "").lower()
    lvl = item.get("level", "").lower()
    return lvl == "beginner" or "fundamentals" in t


# Keywords/patterns that indicate a retired or deprecated certification/exam
DEPRECATED_KEYWORDS = [
    "mta:", "mta ", "mcsa", "mcse", "mcsd",
    "dummy exam",
    "transition",
    "skype for business",
    "sharepoint 2013", "sharepoint 2016",
    "windows server 2012", "windows server 2016",
    " 2007", " 2010", " 2013", " 2016",
    "for talent", "for retail",
    "(pilot)",
]


def is_deprecated(item):
    t = item.get("title", "").lower()
    return any(kw in t for kw in DEPRECATED_KEYWORDS)


# ---------------------------------------------------------------------------
# Source 2 — Skills Hub Blog RSS  (beta exam announcements)
# ---------------------------------------------------------------------------


def get_blog_posts():
    """Return list of recent Skills Hub posts that look like beta/discount news."""
    # Try multiple RSS URL patterns — Microsoft has changed these before
    rss_candidates = [
        SKILLS_HUB_RSS,
        "https://techcommunity.microsoft.com/gxcuf89792/rss/board?board.id=skills-hub-blog",
        "https://techcommunity.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=skills-hub-blog",
    ]

    raw = None
    for url in rss_candidates:
        try:
            raw = fetch(url)
            break
        except Exception as e:
            print(f"[blog] RSS attempt failed ({url}): {e}", file=sys.stderr)

    if raw is None:
        print("[blog] All RSS URLs failed.", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[blog] RSS parse failed: {e}", file=sys.stderr)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    def extract_items(entries, get_title, get_link, get_desc, get_pub):
        for entry in entries:
            title = get_title(entry) or ""
            link = get_link(entry) or ""
            desc = get_desc(entry) or ""
            pub = get_pub(entry) or ""
            blob = (title + " " + desc).lower()
            if any(kw in blob for kw in BETA_KEYWORDS):
                codes = re.findall(r"\b[A-Z]{1,3}-\d{3}\b", title + " " + desc)
                items.append({
                    "id": "blog:" + link,
                    "title": title.strip(),
                    "url": link.strip(),
                    "published": pub.strip(),
                    "exam_codes": list(dict.fromkeys(codes)),
                    "source": "blog",
                })

    # RSS 2.0
    rss_items = root.findall(".//item")
    if rss_items:
        extract_items(
            rss_items,
            lambda e: (e.find("title").text if e.find("title") is not None else ""),
            lambda e: (e.find("link").text if e.find("link") is not None else ""),
            lambda e: (e.find("description").text if e.find("description") is not None else ""),
            lambda e: (e.find("pubDate").text if e.find("pubDate") is not None else ""),
        )
    else:
        # Atom
        extract_items(
            root.findall("atom:entry", ns),
            lambda e: (e.find("atom:title", ns).text if e.find("atom:title", ns) is not None else ""),
            lambda e: (e.find("atom:link", ns).attrib.get("href", "") if e.find("atom:link", ns) is not None else ""),
            lambda e: (e.find("atom:summary", ns).text if e.find("atom:summary", ns) is not None else ""),
            lambda e: (e.find("atom:published", ns).text if e.find("atom:published", ns) is not None else ""),
        )

    print(f"[blog] {len(items)} relevant post(s) found.")
    return items


# ---------------------------------------------------------------------------
# Source 3 — Virtual Training Days events
# ---------------------------------------------------------------------------


def get_vtd_events():
    """
    Fetch VTD events. Tries the JSON API first, falls back to scraping
    the events page if the API shape changes or returns a 404.
    """
    # --- attempt 1: JSON API ---
    try:
        data = fetch_json(VTD_URL)
        raw_list = (
            data if isinstance(data, list)
            else data.get("value") or data.get("events") or data.get("items") or []
        )
        if raw_list:
            events = []
            for ev in raw_list:
                eid = (ev.get("id") or ev.get("eventId")
                       or ev.get("sessionId") or ev.get("title", ""))
                title = ev.get("title") or ev.get("name") or "(untitled)"
                url = (ev.get("url") or ev.get("registrationUrl")
                       or ev.get("eventUrl") or VTD_PAGE)
                start = (ev.get("startDate") or ev.get("startDateTime")
                         or ev.get("startTime") or "")
                events.append({
                    "id": "vtd:" + str(eid),
                    "title": title,
                    "url": url,
                    "date": start[:10] if start else "",
                    "discount": "50% exam discount after attending",
                    "source": "vtd",
                })
            print(f"[vtd] {len(events)} event(s) fetched via API.")
            return events
    except Exception as e:
        print(f"[vtd] API fetch failed: {e} — trying page scrape.", file=sys.stderr)

    # --- attempt 2: scrape the VTD listing page ---
    try:
        html = fetch_text(VTD_PAGE)
        titles = re.findall(r'"(?:title|name)"\s*:\s*"([^"]{15,120})"', html)
        events = []
        seen = set()
        skip_words = ["microsoft", "cookie", "privacy", "surface", "windows", "copyright"]
        for t in titles:
            tl = t.lower()
            if t in seen or any(s in tl for s in skip_words):
                continue
            if "training" in tl or "fundamentals" in tl or "azure" in tl or "virtual" in tl:
                seen.add(t)
                events.append({
                    "id": "vtd:" + t[:80],
                    "title": t,
                    "url": VTD_PAGE,
                    "date": "",
                    "discount": "50% exam discount after attending",
                    "source": "vtd",
                })
        print(f"[vtd] {len(events)} event(s) found via page scrape.")
        return events
    except Exception as e:
        print(f"[vtd] page scrape also failed: {e}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Source 4 — Learn Deals page
# ---------------------------------------------------------------------------


def get_deals():
    """
    Scrape learn.microsoft.com/credentials/certifications/deals for active promos.
    Looks for headings and bold text mentioning discounts/challenges/vouchers.
    """
    DEAL_KEYWORDS = [
        "free", "discount", "voucher", "challenge", "sweepstakes",
        "50%", "80%", "100%", "no cost", "exam offer",
    ]
    try:
        html = fetch_text(DEALS_URL)
    except Exception as e:
        print(f"[deals] fetch failed: {e}", file=sys.stderr)
        return []

    found = []
    # Match h2/h3/h4 headings and <strong>/<b> bold text
    for m in re.finditer(
        r"<(?:h[2-4]|strong|b)[^>]*>(.*?)</(?:h[2-4]|strong|b)>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        raw = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
        text = re.sub(r"\s+", " ", raw)
        if any(kw in text.lower() for kw in DEAL_KEYWORDS) and 8 < len(text) < 200:
            found.append({
                "id": "deal:" + text[:80],
                "title": text,
                "url": DEALS_URL,
                "source": "deals",
            })

    # Deduplicate
    seen = set()
    unique = []
    for d in found:
        if d["id"] not in seen:
            seen.add(d["id"])
            unique.append(d)

    print(f"[deals] {len(unique)} deal heading(s) found.")
    return unique


# ---------------------------------------------------------------------------
# Snapshot / diff
# ---------------------------------------------------------------------------


def load_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return None
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_snapshot(snap):
    os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False, sort_keys=True)


def build_snapshot(catalog, blog_posts, vtd_events, deals):
    snap = {}
    snap.update(catalog)
    for item in blog_posts:
        snap[item["id"]] = item
    for item in vtd_events:
        snap[item["id"]] = item
    for item in deals:
        snap[item["id"]] = item
    return snap


# ---------------------------------------------------------------------------
# Issue body builder
# ---------------------------------------------------------------------------


def fmt_catalog(item):
    tags = [item["kind"]]
    if item.get("level"):
        tags.append(item["level"])
    if is_fundamentals(item):
        tags.append("fundamentals / free-voucher candidate")
    return f"- [{item['title']}]({item['url']})  \n  _{', '.join(tags)}_"


def fmt_blog(item):
    codes = (", ".join(item["exam_codes"])) if item["exam_codes"] else "see post"
    date = f" · {item['published'][:16]}" if item.get("published") else ""
    return f"- [{item['title']}]({item['url']})  \n  _Exam code(s): **{codes}** · 80% discount{date}_"


def fmt_vtd(item):
    date = f" · Starts {item['date']}" if item.get("date") else ""
    return f"- [{item['title']}]({item['url']})  \n  _{item['discount']}{date}_"


def fmt_deal(item):
    return f"- [{item['title']}]({item['url']})"


def build_issue_body(new_catalog, new_blog, new_vtd, new_deals):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## Microsoft cert & voucher update — {today}\n"]

    total = len(new_catalog) + len(new_blog) + len(new_vtd) + len(new_deals)
    lines.append(f"**{total} new item(s)** detected across all sources this week.\n")

    if new_blog:
        lines.append("---")
        lines.append("### 🔥 Beta exams — 80% discount (limited seats, first-come)")
        lines.append(
            "_Codes posted on the [Skills Hub Blog](https://techcommunity.microsoft.com/category/skills-hub/blog/skills-hub-blog). "
            "Register fast — discounted seats fill in hours._\n"
        )
        lines += [fmt_blog(i) for i in new_blog]
        lines.append("")

    if new_deals:
        lines.append("---")
        lines.append("### 🎟️ Active Microsoft deals & sweepstakes")
        lines.append(
            f"_Source: [learn.microsoft.com/credentials/certifications/deals]({DEALS_URL})_\n"
        )
        lines += [fmt_deal(i) for i in new_deals]
        lines.append("")

    if new_vtd:
        lines.append("---")
        lines.append("### 📅 New Virtual Training Days — 50% exam discount after attending")
        lines.append(
            "_Free to attend. Complete the event and get 50% off the related Fundamentals exam, "
            "applied automatically to your Learn profile ~5 business days after the session._\n"
        )
        lines += [fmt_vtd(i) for i in new_vtd]
        lines.append("")

    funds = [i for i in new_catalog if is_fundamentals(i)]
    others = [i for i in new_catalog if not is_fundamentals(i)]

    if funds:
        lines.append("---")
        lines.append("### 🎓 New fundamentals certs & exams (common free-voucher candidates)")
        lines += [fmt_catalog(i) for i in funds]
        lines.append("")

    if others:
        lines.append("---")
        lines.append("### 📋 Other new certifications & exams")
        lines += [fmt_catalog(i) for i in others]
        lines.append("")

    lines.append("---")
    lines.append(
        "> **Tip:** Virtual Training Days vouchers aren't codes — the discount auto-applies at Pearson VUE checkout "
        "when signed in with the same email you registered with. Allow 5 business days after the event.\n"
        f"> Free event listings: [Virtual Training Days]({VTD_PAGE}) · "
        f"[Learn Deals]({DEALS_URL})"
    )

    with open(ISSUE_BODY_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# GitHub env flag
# ---------------------------------------------------------------------------


def set_github_env(key, value):
    gh_env = os.environ.get("GITHUB_ENV")
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    catalog = get_catalog_items()
    blog_posts = get_blog_posts()
    vtd_events = get_vtd_events()
    deals = get_deals()

    new_snap = build_snapshot(catalog, blog_posts, vtd_events, deals)
    prev_snap = load_snapshot()
    save_snapshot(new_snap)

    if prev_snap is None:
        print("First run — baseline saved. No notification sent.")
        set_github_env("HAS_NEWS", "false")
        return 0

    prev_ids = set(prev_snap.keys())
    new_ids = set(new_snap.keys())
    added_ids = new_ids - prev_ids

    new_catalog = [catalog[i] for i in added_ids if i in catalog]
    new_blog = [p for p in blog_posts if p["id"] in added_ids]
    new_vtd = [e for e in vtd_events if e["id"] in added_ids]
    new_deals = [d for d in deals if d["id"] in added_ids]

    total_new = len(new_catalog) + len(new_blog) + len(new_vtd) + len(new_deals)

    if total_new == 0:
        print("Nothing new this week.")
        set_github_env("HAS_NEWS", "false")
        return 0

    print(
        f"New: catalog={len(new_catalog)}, beta_posts={len(new_blog)}, "
        f"vtd={len(new_vtd)}, deals={len(new_deals)}"
    )
    build_issue_body(new_catalog, new_blog, new_vtd, new_deals)
    set_github_env("HAS_NEWS", "true")
    return 0


if __name__ == "__main__":
    sys.exit(main())