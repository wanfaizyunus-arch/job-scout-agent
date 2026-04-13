# Job Scout Agent — Daily Email Digest
Autonomous job search agent for Wan Muhammad Faiz.
Runs daily at **7:00 AM MYT**, scans Indeed for new job listings,
scores each against your profile using Claude AI, and emails a
ranked digest to your Gmail.

---

## What it does each morning
1. Searches Indeed RSS for 5 role types across Malaysia/KL
2. Deduplicates all results
3. Scores every job (0–100%) against your profile with Claude
4. Ranks by match score, filters out weak matches
5. Emails you a digest: top matches, apply recommendations, direct links

---

## Files
```
job_agent.py      ← main script (the agent)
requirements.txt  ← Python dependencies
railway.toml      ← Railway cron schedule config
.env.example      ← environment variable template
```

---

## Deploy on Railway (free, 10 minutes)

### Step 1 — GitHub repo
1. Go to **github.com** → New repository → name it `job-scout-agent`
2. Upload these 4 files: `job_agent.py`, `requirements.txt`, `railway.toml`, `.env.example`
3. Commit

### Step 2 — Railway project
1. Go to **railway.app** → Log in with GitHub
2. Click **New Project → Deploy from GitHub repo**
3. Select `job-scout-agent`
4. Railway auto-detects the cron schedule from `railway.toml`

### Step 3 — Set environment variables
In Railway dashboard → your service → **Variables** tab, add:

| Variable | Value |
|---|---|
| `ANTHROPIC_API_KEY` | your key from console.anthropic.com |
| `GMAIL_ADDRESS` | wanfaizyunus@gmail.com |
| `GMAIL_APP_PASSWORD` | your Gmail App Password (see below) |
| `RECIPIENT_EMAIL` | wanfaizyunus@gmail.com |

### Step 4 — Gmail App Password
Your normal Gmail password won't work for SMTP.
You need a special App Password:
1. Go to **myaccount.google.com/security**
2. Enable **2-Step Verification** (if not already on)
3. Go to **myaccount.google.com/apppasswords**
4. Create new → name it `JobAgent` → copy the 16-character password
5. Paste it as `GMAIL_APP_PASSWORD` in Railway

### Step 5 — Test run
In Railway dashboard → your service → **Deploy** tab → click **Trigger Run**
Check your Gmail inbox — the digest should arrive within 1–2 minutes.

---

## Schedule
```
Cron: 0 23 * * *  (UTC)
=  7:00 AM MYT every day
```
To change the time, edit `railway.toml` → `cronSchedule`.
Use https://crontab.guru to calculate UTC from MYT (MYT = UTC+8).

Examples:
- 6:00 AM MYT → `0 22 * * *`
- 8:00 AM MYT → `0 0 * * *`

---

## Cost estimate
- Railway free tier: 500 hrs/month execution time — more than enough for 1 daily run
- Claude API per run: ~$0.01–0.03 (uses claude-haiku for scoring, very cheap)
- $5 API credits ≈ 6+ months of daily runs

---

## Customise search queries
Edit `SEARCH_QUERIES` in `job_agent.py`:
```python
SEARCH_QUERIES = [
    ("Sales Operations Manager",          "Malaysia"),
    ("Business Development Manager APAC", "Kuala Lumpur"),
    # Add more here...
]
```

---

## Local test (optional)
```bash
pip install anthropic
cp .env.example .env
# fill in your real values in .env
python job_agent.py
```
