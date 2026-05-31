# Microsoft Cert & Voucher Watcher v2

A free, zero-maintenance weekly watcher that monitors **every major Microsoft source**
for free and discounted certification opportunities and emails you when something new appears.

Runs entirely on **GitHub Actions free tier** — no server, no Azure cost, no secrets.

---

## What it watches

| Source | What you get | Discount |
|---|---|---|
| **Skills Hub Blog** (RSS) | New beta exam announcements + discount codes | **80% off** — limited seats, first-come |
| **Virtual Training Days** | New free events with post-attendance exam discount | **50% off** the related exam |
| **Learn Deals page** | Active sweepstakes, challenges, promos | Varies (often 50–100% off) |
| **Learn Catalog API** | New certifications & exams (including new fundamentals) | Flags free-voucher candidates |

### Why these four?

- **Beta exams** are the biggest discount (80% = ~$30 instead of $165) but the codes fill in hours.
  The watcher catches the blog post the moment it's new in the RSS feed.
- **Virtual Training Days** run year-round and give 50% off for just attending a free online session.
  The watcher catches new events so you can register before they fill.
- **Deals page** is where Microsoft lists active sweepstakes (like the Ignite / AI Skills Fest challenges).
  The watcher catches new headings on that page.
- **Catalog API** catches brand-new fundamentals certs — the ones Microsoft most often
  gives away free at events like Build and Ignite.

---

## How notifications work

When the weekly job finds anything new it opens a **GitHub issue** in your repo.
GitHub emails you automatically when an issue is opened in a repo you own.
No webhook, no SMTP, no extra config — it just works.

---

## Setup (5 minutes)

1. **Create a GitHub repo** (private is fine).

2. **Add these files**, keeping the exact structure:
   ```
   check_certs.py
   .github/workflows/cert-check.yml
   data/.gitkeep
   README.md
   ```

3. **Enable write permissions for Actions:**
   Settings → Actions → General → Workflow permissions → ✅ Read and write permissions

4. **Create the `cert-watch` label** (optional but tidy):
   Issues → Labels → New label → name: `cert-watch`, colour: `#0075ca`

5. **Run the first check manually:**
   Actions tab → *Weekly MS Cert & Voucher Watcher* → Run workflow
   
   The first run builds a baseline and opens **no issue** — that's intentional so you aren't
   spammed with the entire catalog. Every run after that reports only what's genuinely new.

6. **Confirm GitHub emails you on issues:**
   github.com → Profile → Settings → Notifications → ensure "Issues" is on for "Participating and @mentions"
   and that you're **Watching** the repo (top-right of the repo page).

---

## Changing the schedule

Edit the `cron:` line in `.github/workflows/cert-check.yml`. Cron is UTC.

| Goal | Cron |
|---|---|
| Every Monday 08:00 UTC (default) | `0 8 * * 1` |
| Every Friday 07:00 UTC | `0 7 * * 5` |
| Twice a week (Mon + Thu) | `0 8 * * 1,4` |

---

## Notes

- **Zero pip dependencies** — everything uses Python 3.12 stdlib only.
- **No secrets required** — all sources are public and unauthenticated.
- **Cost:** a ~1-minute weekly job is far inside the 2,000 min/month free-tier allowance
  for private repos (unlimited for public repos).
- **GitHub may pause** scheduled workflows after 60 days of repo inactivity.
  The weekly snapshot commit normally keeps it alive on its own.
- **Beta exam codes fill fast.** The watcher fires weekly; if a beta drops mid-week
  you may miss the discount window. For real-time beta alerts, also follow the
  [Skills Hub Blog](https://techcommunity.microsoft.com/category/skills-hub/blog/skills-hub-blog)
  directly or set the RSS feed in an RSS reader.
