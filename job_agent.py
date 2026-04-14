"""
Job Search Agent — Daily Email Digest v2
Uses Adzuna API (free tier) instead of Indeed RSS.
Faiz's autonomous job scout: runs daily, finds new jobs, scores each
against your profile with Claude AI, and emails a ranked digest.
"""

import os
import re
import json
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import anthropic

# ─── CONFIGURATION ────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GMAIL_ADDRESS     = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PW      = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL   = os.environ.get("RECIPIENT_EMAIL", GMAIL_ADDRESS)
RAPIDAPI_KEY      = os.environ["RAPIDAPI_KEY"]

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

MATCHING CRITERIA (apply these strictly when scoring):

Salary Expectation: MYR 13,000/month minimum. Roles below this or clearly
junior/entry-level should score max 40 and be marked "Skip".

Preferred Industries (in priority order):
1. Technology / IT / Semiconductor (HIGHEST priority — direct experience)
2. BPO / Shared Services / Outsourcing (HIGH priority — direct experience via Concentrix)
3. Energy / Oil & Gas / Utilities (MEDIUM priority — good fit for ops roles)
4. Telecommunications (MEDIUM priority — transferable fit)
Roles in unrelated industries (retail, F&B, property, healthcare) score max 55.

Location: Kuala Lumpur or Selangor strongly preferred. Remote or hybrid acceptable.
Roles requiring relocation outside Malaysia should score max 35.

Seniority: Manager level and above only. Executive, Associate, or fresh grad
roles should score max 30 and be marked "Skip".

Scoring boost: Add +10 to score if role mentions Salesforce, Power BI, APAC,
regional, renewals, channel, or inside sales — these are direct skill matches.
"""

# ─── JOB SEARCH QUERIES ───────────────────────────────────────────
SEARCH_QUERIES = [
    "Sales Operations Manager",
    "Sales Support Operations Manager",
    "Business Development Manager",
    "Regional Sales Manager",
    "Inside Sales Manager",
    "Channel Sales Manager",
]
SEARCH_LOCATION = "Malaysia"

# No location filter — "Malaysia" is embedded in every query
# Location is shown clearly in the email digest for manual review

# ─── JSEARCH API (RapidAPI) ──────────────────────────────────────
def search_jsearch(query: str, location: str = "Malaysia", max_results: int = 8) -> list[dict]:
    """Search jobs via JSearch API — pulls from Google Jobs, LinkedIn, Indeed, JobStreet."""
    params = urllib.parse.urlencode({
        "query":          f"{query} in {location}",
        "page":           "1",
        "num_pages":      "1",
        "date_posted":    "week",
        "employment_types": "FULLTIME,CONTRACTOR",
    })
    url = f"https://jsearch.p.rapidapi.com/search?{params}"
    headers = {
        "X-RapidAPI-Key":  RAPIDAPI_KEY,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
        "User-Agent":      "JobAgent/3.0"
    }
    try:
        req  = urllib.request.Request(url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        jobs = []
        for r in data.get("data", [])[:max_results]:
            title    = r.get("job_title", "").strip()
            company  = r.get("employer_name", "Unknown")
            city     = r.get("job_city", "")
            country  = r.get("job_country", "")
            loc_str  = ", ".join(filter(None, [city, country])) or "Malaysia"
            url_link = r.get("job_apply_link") or r.get("job_google_link", "")
            desc     = r.get("job_description", "")[:400]
            salary   = ""
            s_min = r.get("job_min_salary")
            s_max = r.get("job_max_salary")
            s_cur = r.get("job_salary_currency", "")
            if s_min and s_max:
                salary = f"{s_cur} {s_min:,.0f}-{s_max:,.0f}"
            if title and url_link:
                jobs.append({
                    "title":    title,
                    "company":  company,
                    "location": loc_str,
                    "salary":   salary,
                    "url":      url_link,
                    "snippet":  desc,
                    "query":    query,
                })
        print(f"    JSearch returned {len(jobs)} results")
        return jobs
    except Exception as e:
        print(f"    JSearch error: {e}")
        return []

# ─── DEDUPLICATE ──────────────────────────────────────────────────
def deduplicate(jobs: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for j in jobs:
        key = j["url"].split("?")[0]
        if key not in seen:
            seen.add(key)
            unique.append(j)
    return unique


# ─── SCORE WITH CLAUDE ────────────────────────────────────────────
def score_batch(client, batch: list[dict], offset: int) -> list[dict]:
    """Score a batch of up to 10 jobs."""
    jobs_text = "\n\n".join([
        f"JOB {i+1}:\nTitle: {j['title']}\nCompany: {j['company']}\n"
        f"Location: {j.get('location','')}\nDescription: {j['snippet'][:200]}"
        for i, j in enumerate(batch)
    ])
    prompt = f"""Score these {len(batch)} jobs against the candidate profile below.
Pay close attention to the MATCHING CRITERIA section — apply salary, industry,
seniority and location rules strictly when assigning scores.

CANDIDATE PROFILE:
{PROFILE}

JOBS TO SCORE:
{jobs_text}

Scoring rules summary:
- Below MYR 13k or clearly junior/entry → max score 40, mark Skip
- Tech/IT/Semiconductor industry → score boost, highest priority
- BPO/Shared Services → score boost, high priority (direct Concentrix experience)
- Energy/Oil & Gas → acceptable, slight boost
- Telecom → acceptable
- Unrelated industry (retail, F&B, property, healthcare) → max score 55
- Outside Malaysia (relocation required) → max score 35
- Manager level+ only; exec/associate/fresh grad → max score 30, mark Skip
- Direct skill match (Salesforce, Power BI, APAC, regional, renewals, channel) → +10 boost

Return a JSON array with exactly {len(batch)} objects. Each object:
- job_number (int, 1 to {len(batch)})
- match_score (int, 0-100)
- match_reason (string, 1 specific sentence explaining the score)
- key_requirements (array of exactly 3 strings from the job description)
- apply_recommendation (string: "Strong Apply", "Apply", "Maybe", or "Skip")

Return ONLY the JSON array. Start with [ and end with ]. No other text."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = msg.content[0].text.strip()
    raw = re.sub(r"^```json\s*|^```\s*|```\s*$", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    arr_match = re.search(r"\[.*\]", raw, re.DOTALL)
    if arr_match:
        raw = arr_match.group(0)
    scores = json.loads(raw)
    for s in scores:
        idx = offset + s.get("job_number", 0) - 1
        if 0 <= idx < len(batch) + offset:
            real_idx = s.get("job_number", 0) - 1
            if 0 <= real_idx < len(batch):
                batch[real_idx]["match_score"]         = int(s.get("match_score", 50))
                batch[real_idx]["match_reason"]         = s.get("match_reason", "")
                batch[real_idx]["key_requirements"]     = s.get("key_requirements", [])
                batch[real_idx]["apply_recommendation"] = s.get("apply_recommendation", "Maybe")
    return batch


def score_jobs_with_claude(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Score in batches of 8 to avoid token limit issues
    BATCH = 8
    for i in range(0, len(jobs), BATCH):
        batch = jobs[i:i+BATCH]
        try:
            scored = score_batch(client, batch, i)
            jobs[i:i+BATCH] = scored
            print(f"  Scored batch {i//BATCH + 1} ({len(batch)} jobs)")
        except Exception as e:
            print(f"  Batch {i//BATCH + 1} scoring error: {e}")
            for j in batch:
                j.setdefault("match_score", 50)
                j.setdefault("match_reason", "Score unavailable")
                j.setdefault("key_requirements", [])
                j.setdefault("apply_recommendation", "Review manually")
    return jobs


# ─── BUILD HTML EMAIL ─────────────────────────────────────────────
def build_email_html(jobs: list[dict], today_str: str) -> str:
    def score_color(s):
        return "#2ec4a4" if s >= 80 else "#f0a030" if s >= 60 else "#888780"

    def rec_badge(rec):
        colors = {
            "Strong Apply": ("#2ec4a4","#0a2e28"),
            "Apply":        ("#4a9eff","#0a1a2e"),
            "Maybe":        ("#f0a030","#2e1a00"),
            "Skip":         ("#555555","#1a1a1a"),
        }
        bg, fg = colors.get(rec, ("#555555","#1a1a1a"))
        return (f'<span style="background:{bg};color:{fg};font-size:10px;font-weight:600;'
                f'padding:3px 10px;border-radius:12px;font-family:monospace;'
                f'letter-spacing:0.06em">{rec.upper()}</span>')

    sorted_jobs = sorted(jobs, key=lambda j: j.get("match_score",0), reverse=True)
    top_jobs    = [j for j in sorted_jobs if j.get("apply_recommendation") != "Skip"][:10]

    job_cards = ""
    for i, j in enumerate(top_jobs):
        score = j.get("match_score", 0)
        color = score_color(score)
        reqs  = j.get("key_requirements", [])
        reqs_html = "".join(
            f"<li style='margin:3px 0;font-size:12px;color:#aaaaaa'>{r}</li>"
            for r in reqs[:3]
        )
        sal_tag = (f'<span style="background:#1a2e1a;color:#5cb87a;font-size:10px;'
                   f'padding:2px 8px;border-radius:10px;font-family:monospace;margin-left:6px">'
                   f'{j["salary"]}</span>') if j.get("salary") else ""
        posted_tag = (
            f'<span style="background:#1a1a2e;color:#5f5d58;font-size:10px;padding:2px 8px;border-radius:10px;font-family:monospace;margin-left:6px">"'
            f'Posted: {j.get("posted","")}</span>'
        ) if j.get("posted") else ""

        job_cards += f"""
        <div style="background:#1a1d27;border:1px solid #2a2d3a;border-left:3px solid {color};
                    border-radius:8px;padding:16px 18px;margin-bottom:12px">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;
                      margin-bottom:8px;flex-wrap:wrap;gap:8px">
            <div style="flex:1">
              <div style="font-family:Georgia,serif;font-size:15px;font-weight:bold;
                          color:#e8e6df;margin-bottom:3px">{i+1}. {j['title']}</div>
              <div style="font-size:12px;color:{color};font-weight:600">
                {j.get('company','Unknown')}{sal_tag}{posted_tag}
              </div>
              <div style="font-size:11px;color:#5f5d58;margin-top:2px">
                📍 {j.get('location','')}
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

    strong_count = len([j for j in jobs if j.get("match_score",0) >= 70])
    apply_count  = len([j for j in jobs if j.get("apply_recommendation") in ["Strong Apply","Apply"]])

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d0f14;font-family:'Helvetica Neue',Arial,sans-serif">
  <div style="max-width:620px;margin:0 auto;padding:20px 16px">
    <div style="background:#13151c;border:1px solid #22263a;border-radius:10px;
                padding:20px 24px;margin-bottom:16px;text-align:center">
      <div style="font-size:11px;color:#f0a030;font-family:monospace;letter-spacing:0.14em;
                  margin-bottom:6px">DAILY JOB DIGEST</div>
      <div style="font-size:22px;font-weight:bold;color:#e8e6df;font-family:Georgia,serif">
        Job Search Agent
      </div>
      <div style="font-size:12px;color:#5f5d58;margin-top:4px">{today_str} · Malaysia Market</div>
    </div>
    <div style="background:#13151c;border:1px solid #22263a;border-radius:8px;
                padding:12px 16px;margin-bottom:16px;text-align:center">
      <table width="100%"><tr>
        <td style="text-align:center">
          <div style="font-size:22px;font-weight:bold;color:#f0a030;font-family:monospace">{len(jobs)}</div>
          <div style="font-size:10px;color:#5f5d58;font-family:monospace;text-transform:uppercase">Scanned</div>
        </td>
        <td style="text-align:center">
          <div style="font-size:22px;font-weight:bold;color:#2ec4a4;font-family:monospace">{strong_count}</div>
          <div style="font-size:10px;color:#5f5d58;font-family:monospace;text-transform:uppercase">Strong Match</div>
        </td>
        <td style="text-align:center">
          <div style="font-size:22px;font-weight:bold;color:#4a9eff;font-family:monospace">{apply_count}</div>
          <div style="font-size:10px;color:#5f5d58;font-family:monospace;text-transform:uppercase">Apply</div>
        </td>
      </tr></table>
    </div>
    <div style="font-size:11px;color:#5f5d58;font-family:monospace;
                letter-spacing:0.1em;text-transform:uppercase;margin-bottom:10px">
      Top Matches — Ranked by Fit
    </div>
    {job_cards}
    <div style="background:#13151c;border:1px solid #22263a;border-radius:8px;
                padding:14px 18px;margin-top:16px;text-align:center">
      <div style="font-size:11px;color:#5f5d58;margin-bottom:8px">
        Found a role? Copy the JD and paste into your
      </div>
      <div style="font-size:13px;font-weight:bold;color:#f0a030">
        Job Search Command Center → JD Analyzer
      </div>
      <div style="font-size:10px;color:#3a3d4a;margin-top:10px;font-family:monospace">
        Job Scout Agent · {today_str}
      </div>
    </div>
  </div>
</body>
</html>"""


# ─── SEND EMAIL ───────────────────────────────────────────────────
def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PW)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())
    print(f"  ✓ Email sent to {RECIPIENT_EMAIL}")


# ─── MAIN ─────────────────────────────────────────────────────────
def main():
    today_str = datetime.now().strftime("%A, %d %B %Y")
    print(f"\n{'='*50}")
    print(f"  Job Scout Agent v2 — {today_str}")
    print(f"{'='*50}")

    all_jobs = []
    for query in SEARCH_QUERIES:
        print(f"\n  Searching: '{query}' in {SEARCH_LOCATION}...")
        results = search_jsearch(query, location=SEARCH_LOCATION, max_results=8)
        print(f"  Found {len(results)} listings")
        all_jobs.extend(results)

    all_jobs = deduplicate(all_jobs)
    print(f"\n  Total unique jobs: {len(all_jobs)}")

    if not all_jobs:
        print("  No jobs found — skipping email.")
        return

    print(f"\n  Scoring {len(all_jobs)} jobs with Claude...")
    scored = score_jobs_with_claude(all_jobs)
    strong = len([j for j in scored if j.get("match_score", 0) >= 70])
    print(f"  Strong matches (70%+): {strong}")

    print("\n  Sending email digest...")
    html    = build_email_html(scored, today_str)
    subject = (f"🎯 {strong} Strong Job Match{'es' if strong != 1 else ''} Today — "
               f"{datetime.now().strftime('%d %b %Y')}")
    send_email(subject, html)
    print(f"\n  Done.\n")


if __name__ == "__main__":
    main()
