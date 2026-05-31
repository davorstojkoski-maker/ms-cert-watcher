#!/usr/bin/env python3
"""
Microsoft Certification & Voucher Watcher — v2
================================================
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

HEADERS = {"User-Agent": "ms-cert-watcher/2.0 (github-actions)"}
SNAPSHOT_FILE = "data/snapshot.json"
ISSUE_BODY_FILE = "issue_body.md"

CATALOG_URL = (
    "https://learn.microsoft.com/api/catalog/?type=certifications,exams&locale=en-us"
)
SKILLS_HUB_RSS = (
    "https://techcommunity.microsoft.com/gxcuf89792/rss/board?board.id=skills-hub-blog"
)
VTD_URL = (
    "https://www.microsoft.com/en-us/events/api/v1/events?"
    + urlencode(
        {
            "scenario": "mvtd",
            "filters": "primary-language:english",
            "top": 50,
        }
    )
)
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
            out[uid] = {
                "id": uid,
                "title": item.get("title", "(untitled)"),
                "kind": kind,
                "level": ((item.get("levels") or [""])[0]),
                "products": item.get("products") or [],
                "url": item.get("url") or "https://learn.microsoft.com/credentials/",
                "source": "catalog",
            }
    print(f"[catalog] {len(out)} items fetched.")
    return out


def is_fundamentals(item):
    t = item.get("title", "").lower()
    lvl = item.get("level", "").lower()
    return lvl == "beginner" or "fundamentals" in t


# ---------------------------------------------------------------------------
# Source 2 — Skills Hub Blog RSS  (beta exam announcements)
# ---------------------------------------------------------------------------


def get_blog_posts():
    """Return list of recent Skills Hub posts that look like beta/discount news."""
    try:
        raw = fetch(SKILLS_HUB_RSS)
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"[blog] RSS fetch/parse failed: {e}", file=sys.stderr)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    # Try RSS 2.0 format first
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        pub_el = item.find("pubDate")
        title = (title_el.text or "") if title_el is not None else ""
        link = (link_el.text or "") if link_el is not None else ""
        desc = (desc_el.text or "") if desc_el is not None else ""
        pub = (pub_el.text or "") if pub_el is not None else ""
        blob = (title + " " + desc).lower()
        if any(kw in blob for kw in BETA_KEYWORDS):
            # Try to extract an exam code like AZ-900, AI-103, SC-500, AB-250 …
            codes = re.findall(r"\b[A-Z]{1,3}-\d{3}\b", title + " " + desc)
            items.append({
                "id": "blog:" + link,
                "title": title.strip(),
                "url": link.strip(),
                "published": pub.strip(),
                "exam_codes": list(dict.fromkeys(codes)),  # deduplicated
                "source": "blog",
            })

    # Fallback: Atom format
    if not items:
        for entry in root.findall("atom:entry", ns):
            t = entry.find("atom:title", ns)
            l = entry.find("atom:link", ns)
            s = entry.find("atom:summary", ns)
            p = entry.find("atom:published", ns)
            title = (t.text or "") if t is not None else ""
            link = (l.attrib.get("href", "") if l is not None else "")
            summary = (s.text or "") if s is not None else ""
            pub = (p.text or "") if p is not None else ""
            blob = (title + " " + summary).lower()
            if any(kw in blob for kw in BETA_KEYWORDS):
                codes = re.findall(r"\b[A-Z]{1,3}-\d{3}\b", title + " " + summary)
                items.append({
                    "id": "blog:" + link,
                    "title": title.strip(),
                    "url": link.strip(),
                    "published": pub.strip(),
                    "exam_codes": list(dict.fromkeys(codes)),
                    "source": "blog",
                })

    print(f"[blog] {len(items)} relevant post(s) found.")
    return items


# ---------------------------------------------------------------------------
# Source 3 — Virtual Training Days events
# ---------------------------------------------------------------------------


def get_vtd_events():
    """
    Fetch VTD events from the Microsoft Events API.
    Returns list of event dicts with id, title, url, date, discount.
    Falls back gracefully if the API shape changes.
    """
    try:
        data = fetch_json(VTD_URL)
    except Exception as e:
        print(f"[vtd] fetch failed: {e}", file=sys.stderr)
        return []

    events = []
    # The events API returns {"value": [...]} or just a list
    raw_list = data if isinstance(data, list) else data.get("value", [])
    for ev in raw_list:
        eid = ev.get("id") or ev.get("eventId") or ev.get("title", "")
        title = ev.get("title") or ev.get("name") or "(untitled)"
        url = ev.get("url") or ev.get("registrationUrl") or "https://www.microsoft.com/events"
        start = ev.get("startDate") or ev.get("startDateTime") or ""
        events.append({
            "id": "vtd:" + str(eid),
            "title": title,
            "url": url,
            "date": start[:10] if start else "",
            "discount": "50% exam discount after attending",
            "source": "vtd",
        })
    print(f"[vtd] {len(events)} event(s) fetched.")
    return events


# ---------------------------------------------------------------------------
# Source 4 — Learn Deals page
# ---------------------------------------------------------------------------


def get_deals():
    """
    Scrape learn.microsoft.com/credentials/certifications/deals for active promos.
    We look for headings / paragraphs mentioning active challenges, sweepstakes,
    free or discounted exam offers.
    """
    DEAL_KEYWORDS = [
        "free", "discount", "voucher", "challenge", "sweepstakes",
        "50%", "80%", "100%", "no cost",
    ]
    try:
        html = fetch_text(DEALS_URL)
    except Exception as e:
        print(f"[deals] fetch failed: {e}", file=sys.stderr)
        return []

    # Extract <h2>/<h3> headings and nearby text as crude deal titles
    # This is intentionally simple so it doesn't break on minor HTML changes
    found = []
    for m in re.finditer(
        r"<h[23][^>]*>(.*?)</h[23]>",
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        raw = re.sub(r"<[^>]+>", " ", m.group(1)).strip()
        text = re.sub(r"\s+", " ", raw)
        if any(kw in text.lower() for kw in DEAL_KEYWORDS) and len(text) > 8:
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
        f"> Free event listings: [Virtual Training Days](https://www.microsoft.com/events/category/microsoft-virtual-training-days) · "
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
    # Fetch all sources
    catalog = get_catalog_items()
    blog_posts = get_blog_posts()
    vtd_events = get_vtd_events()
    deals = get_deals()

    # Build new snapshot
    new_snap = build_snapshot(catalog, blog_posts, vtd_events, deals)

    # Load previous
    prev_snap = load_snapshot()

    # Save updated snapshot regardless
    save_snapshot(new_snap)

    if prev_snap is None:
        print("First run — baseline saved. No notification sent.")
        set_github_env("HAS_NEWS", "false")
        return 0

    # Diff each source
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
