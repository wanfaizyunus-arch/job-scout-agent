"""
Job Search Agent — Daily Email Digest
Faiz's autonomous job scout: runs daily, finds new jobs, scores each
against your profile with Claude AI, and emails a ranked digest.

Deploy on Railway.app as a cron job: runs 7am MYT every day.
"""

import os
import re
import json
import smtplib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

# ─── CONFIGURATION (set these as env vars on Railway) ─────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_ADDRESS     = os.environ["GMAIL_ADDRESS"]       # your Gmail
GMAIL_APP_PW      = os.environ["GMAIL_APP_PASSWORD"]  # Gmail App Password
RECIPIENT_EMAIL   = os.environ.get("RECIPIENT_EMAIL", GMAIL_ADDRESS)

# ─── FAIZ'S PROFILE ───────────────────────────────────────────────
PROFILE = """
Name: Wan Muhammad Faiz Bin Wan Yunus
Location: Dengkil, Selangor, Malaysia
Experience: 12+ years in Sales & Operations, APJ/APAC
Current: APJ Sales Manager – Inside Sales at AMD via Concentrix (2024–present)
  - Exceeded annual targets by 228%, generated $2M+ quarterly revenue
  - Led 9-member team, 96% employee satisfaction score
Previous: APAC Renewal Lead at Hitachi Vantara (2019–2023)
  - Managed $100M pipeline, 16-member team across APAC
Previous: Senior Manager APAC AER Sales & Operations at Hitachi Vantara (2015–2019)
  - Led 50+ professionals, exceeded P&L targets
Education: BBA Accounting, Universiti Kebangsaan Malaysia (UKM)
Skills: Salesforce CRM, Power BI, Sales Operations, Team Leadership,
        Channel Partner Management, Strategic Planning, P&L Management
Languages: English, Bahasa Malaysia
Target roles: Sales Operations Manager, Sales Support Operations Manager,
              Business Development Manager APAC, Regional Sales Lead
"""

# ─── JOB SEARCH QUERIES ───────────────────────────────────────────
# Each entry: (search_query, location)
SEARCH_QUERIES = [
    ("Sales Operations Manager",          "Malaysia"),
    ("Sales Support Manager",             "Kuala Lumpur"),
    ("Business Development Manager APAC", "Malaysia"),
    ("Regional Sales Manager",            "Malaysia"),
    ("Inside Sales Manager",              "Kuala Lumpur"),
]

# ─── INDEED RSS FEED SEARCH ───────────────────────────────────────
def search_indeed_rss(query: str, location: str, max_results: int = 8) -> list[dict]:
    """Fetch jobs from Indeed's public RSS feed."""
    params = urllib.parse.urlencode({
        "q":    query,
        "l":    location,
        "sort": "date",
        "limit": max_results,
    })
    url = f"https://www.indeed.com/rss?{params}"
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; JobAgent/1.0)"
    }

    try:
        req  = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        xml  = resp.read().decode("utf-8", errors="ignore")
        root = ET.fromstring(xml)
        channel = root.find("channel")
        if channel is None:
            return []

        jobs = []
        for item in channel.findall("item")[:max_results]:
            title   = (item.findtext("title")       or "").strip()
            link    = (item.findtext("link")        or "").strip()
            company = (item.findtext("source")      or "").strip()
            pub     = (item.findtext("pubDate")     or "").strip()
            desc    = (item.findtext("description") or "").strip()
            # Strip HTML tags from description
            desc_clean = re.sub(r"<[^>]+>", " ", desc)
            desc_clean = re.sub(r"\s+", " ", desc_clean).strip()[:400]

            if title and link:
                jobs.append({
                    "title":   title,
                    "company": company,
                    "url":     link,
                    "date":    pub,
                    "snippet": desc_clean,
                    "query":   query,
                })
        return jobs

    except Exception as e:
        print(f"  RSS fetch error for '{query}': {e}")
        return []


# ─── DEDUPLICATE JOBS ─────────────────────────────────────────────
def deduplicate(jobs: list[dict]) -> list[dict]:
    """Remove duplicate jobs by URL."""
    seen = set()
    unique = []
    for j in jobs:
        key = j["url"].split("?")[0]  # strip query params
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


# ─── SCORE JOBS WITH CLAUDE ───────────────────────────────────────
def score_jobs_with_claude(jobs: list[dict]) -> list[dict]:
    """Score all jobs in a single Claude API call to save credits."""
    if not jobs:
        return []

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    jobs_text = "\n\n".join([
        f"JOB {i+1}:\nTitle: {j['title']}\nCompany: {j['company']}\n"
        f"Description: {j['snippet']}\nURL: {j['url']}"
        for i, j in enumerate(jobs)
    ])

    prompt = f"""You are a job match analyst. Score each job against this candidate profile.

CANDIDATE PROFILE:
{PROFILE}

JOBS TO SCORE:
{jobs_text}

For each job return a JSON array. Each object must have:
- job_number (int)
- match_score (0-100)
- match_reason (1 sentence, specific)
- key_requirements (3 bullet points extracted from description)
- apply_recommendation (one of: "Strong Apply", "Apply", "Maybe", "Skip")

Return ONLY valid JSON array, no other text."""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",  # fast + cheap for scoring
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```json|^```|```$", "", raw, flags=re.MULTILINE).strip()
        scores = json.loads(raw)

        # Merge scores back into jobs
        for s in scores:
            idx = s.get("job_number", 0) - 1
            if 0 <= idx < len(jobs):
                jobs[idx]["match_score"]         = s.get("match_score", 0)
                jobs[idx]["match_reason"]         = s.get("match_reason", "")
                jobs[idx]["key_requirements"]     = s.get("key_requirements", [])
                jobs[idx]["apply_recommendation"] = s.get("apply_recommendation", "Maybe")

        return jobs

    except Exception as e:
        print(f"  Claude scoring error: {e}")
        # Return jobs with default scores if Claude fails
        for j in jobs:
            j.setdefault("match_score", 50)
            j.setdefault("match_reason", "Unable to score — Claude API error")
            j.setdefault("key_requirements", [])
            j.setdefault("apply_recommendation", "Review manually")
        return jobs


# ─── BUILD HTML EMAIL ─────────────────────────────────────────────
def build_email_html(jobs: list[dict], today_str: str) -> str:
    """Build a clean, mobile-friendly HTML email digest."""

    def score_color(score: int) -> str:
        if score >= 80: return "#2ec4a4"   # teal — strong match
        if score >= 60: return "#f0a030"   # amber — good match
        return "#888780"                   # gray — weak

    def rec_badge(rec: str) -> str:
        colors = {
            "Strong Apply": ("#2ec4a4", "#0a2e28"),
            "Apply":        ("#4a9eff", "#0a1a2e"),
            "Maybe":        ("#f0a030", "#2e1a00"),
            "Skip":         ("#555555", "#1a1a1a"),
        }
        bg, fg = colors.get(rec, ("#555555", "#1a1a1a"))
        return (f'<span style="background:{bg};color:{fg};font-size:10px;'
                f'font-weight:600;padding:3px 10px;border-radius:12px;'
                f'font-family:monospace;letter-spacing:0.06em">{rec.upper()}</span>')

    # Sort by match score descending
    sorted_jobs = sorted(jobs, key=lambda j: j.get("match_score", 0), reverse=True)
    top_jobs    = [j for j in sorted_jobs if j.get("apply_recommendation") != "Skip"][:10]
    skip_count  = len(jobs) - len(top_jobs)

    job_cards = ""
    for i, j in enumerate(top_jobs):
        score   = j.get("match_score", 0)
        color   = score_color(score)
        reqs    = j.get("key_requirements", [])
        reqs_html = "".join(f"<li style='margin:3px 0;font-size:12px;color:#aaaaaa'>{r}</li>"
                            for r in reqs[:3]) if reqs else ""

        job_cards += f"""
        <div style="background:#1a1d27;border:1px solid #2a2d3a;border-left:3px solid {color};
                    border-radius:8px;padding:16px 18px;margin-bottom:12px">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;
                      margin-bottom:8px;flex-wrap:wrap;gap:8px">
            <div style="flex:1">
              <div style="font-family:Georgia,serif;font-size:15px;font-weight:bold;
                          color:#e8e6df;margin-bottom:3px">
                {i+1}. {j['title']}
              </div>
              <div style="font-size:12px;color:{color};font-weight:600">
                {j.get('company','Unknown Company')}
              </div>
            </div>
            <div style="text-align:right;flex-shrink:0">
              <div style="font-family:monospace;font-size:22px;font-weight:bold;
                          color:{color};line-height:1">{score}%</div>
              <div style="font-size:9px;color:#666;margin-top:2px">MATCH</div>
            </div>
          </div>
          <div style="margin-bottom:8px">{rec_badge(j.get('apply_recommendation','Maybe'))}</div>
          <p style="font-size:12px;color:#9b9890;margin:6px 0;font-style:italic">
            {j.get('match_reason','')}
          </p>
          {f'<ul style="margin:8px 0;padding-left:16px">{reqs_html}</ul>' if reqs_html else ''}
          <div style="margin-top:10px">
            <a href="{j['url']}" style="background:#f0a030;color:#000;font-size:11px;
               font-weight:600;padding:6px 14px;border-radius:5px;text-decoration:none;
               font-family:monospace;letter-spacing:0.06em">VIEW JOB ↗</a>
          </div>
        </div>"""

    total_searched = len(jobs) + skip_count

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d0f14;font-family:'Helvetica Neue',Arial,sans-serif">
  <div style="max-width:620px;margin:0 auto;padding:20px 16px">

    <!-- Header -->
    <div style="background:#13151c;border:1px solid #22263a;border-radius:10px;
                padding:20px 24px;margin-bottom:16px;text-align:center">
      <div style="font-size:11px;color:#f0a030;font-family:monospace;letter-spacing:0.14em;
                  margin-bottom:6px">DAILY JOB DIGEST</div>
      <div style="font-size:22px;font-weight:bold;color:#e8e6df;font-family:Georgia,serif">
        Job Search Agent
      </div>
      <div style="font-size:12px;color:#5f5d58;margin-top:4px">{today_str} · Malaysia Market</div>
    </div>

    <!-- Stats bar -->
    <div style="background:#13151c;border:1px solid #22263a;border-radius:8px;
                padding:12px 16px;margin-bottom:16px;
                display:flex;justify-content:space-around;text-align:center">
      <div>
        <div style="font-size:22px;font-weight:bold;color:#f0a030;font-family:monospace">
          {len(jobs)}
        </div>
        <div style="font-size:10px;color:#5f5d58;font-family:monospace;
                    text-transform:uppercase;letter-spacing:0.08em">Scanned</div>
      </div>
      <div>
        <div style="font-size:22px;font-weight:bold;color:#2ec4a4;font-family:monospace">
          {len([j for j in jobs if j.get('match_score',0) >= 70])}
        </div>
        <div style="font-size:10px;color:#5f5d58;font-family:monospace;
                    text-transform:uppercase;letter-spacing:0.08em">Strong Match</div>
      </div>
      <div>
        <div style="font-size:22px;font-weight:bold;color:#4a9eff;font-family:monospace">
          {len([j for j in jobs if j.get('apply_recommendation') in ['Strong Apply','Apply']])}
        </div>
        <div style="font-size:10px;color:#5f5d58;font-family:monospace;
                    text-transform:uppercase;letter-spacing:0.08em">Apply</div>
      </div>
    </div>

    <!-- Job cards -->
    <div style="font-size:11px;color:#5f5d58;font-family:monospace;
                letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">
      Top Matches — Ranked by Fit
    </div>
    {job_cards}

    <!-- Footer -->
    <div style="background:#13151c;border:1px solid #22263a;border-radius:8px;
                padding:14px 18px;margin-top:16px;text-align:center">
      <div style="font-size:11px;color:#5f5d58;margin-bottom:8px">
        Found a role you like? Copy its JD and paste into your
      </div>
      <div style="font-size:13px;font-weight:bold;color:#f0a030">
        Job Search Command Center → JD Analyzer
      </div>
      <div style="font-size:10px;color:#3a3d4a;margin-top:10px;font-family:monospace">
        Job Scout Agent · Running daily 7:00 AM MYT · {today_str}
      </div>
    </div>

  </div>
</body>
</html>"""


# ─── SEND EMAIL ───────────────────────────────────────────────────
def send_email(subject: str, html_body: str):
    """Send via Gmail SMTP using an App Password."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    print(f"  Email sent to {RECIPIENT_EMAIL}")


# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    today_str = datetime.now().strftime("%A, %d %B %Y")
    print(f"\n{'='*50}")
    print(f"  Job Scout Agent — {today_str}")
    print(f"{'='*50}")

    # 1. Collect jobs from all search queries
    all_jobs = []
    for query, location in SEARCH_QUERIES:
        print(f"\n  Searching: '{query}' in {location}...")
        results = search_indeed_rss(query, location, max_results=6)
        print(f"  Found {len(results)} listings")
        all_jobs.extend(results)

    all_jobs = deduplicate(all_jobs)
    print(f"\n  Total unique jobs: {len(all_jobs)}")

    if not all_jobs:
        print("  No jobs found — skipping email.")
        return

    # 2. Score with Claude
    print(f"\n  Scoring {len(all_jobs)} jobs with Claude...")
    scored_jobs = score_jobs_with_claude(all_jobs)
    strong = len([j for j in scored_jobs if j.get("match_score", 0) >= 70])
    print(f"  Strong matches (70%+): {strong}")

    # 3. Build and send email
    print("\n  Building email digest...")
    html   = build_email_html(scored_jobs, today_str)
    subject = (f"🎯 {strong} Strong Job Match{'es' if strong != 1 else ''} Today — "
               f"{datetime.now().strftime('%d %b %Y')}")
    print(f"  Sending: {subject}")
    send_email(subject, html)

    print(f"\n  Done. Agent run complete.\n")


if __name__ == "__main__":
    main()
