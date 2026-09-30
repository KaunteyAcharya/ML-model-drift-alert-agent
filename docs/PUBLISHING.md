# Verify → record → publish

## 1. Verify it works (about 15 minutes)

Tick these off in order. Each step proves one link in the chain.

| # | Check | How | Expected |
|---|---|---|---|
| 1 | Containers up | `docker compose ps` | postgres, drift-monitor and n8n **healthy**; grafana **running** |
| 2 | Monitor alive | open http://localhost:8010/health | `{"status":"ok", ... "database":true}` |
| 3 | Drift + DB | `docker compose exec drift-monitor python -m app.cli run --scenario calibration --summary` | `"alert": "critical"` and a `report_url` |
| 4 | Evidently report | open the `report_url` from step 3 | Dataset Drift page with red histograms |
| 5 | Ollama reachable | the check in [SETUP_OLLAMA.md](SETUP_OLLAMA.md) step 3 | JSON that lists `llama3.1` |
| 6 | Full workflow | n8n (http://localhost:5678) → Config: `scenario = calibration` → **Execute workflow** | every node green; *Ollama: explain drift* shows text |
| 7 | Slack | your #ml-alerts channel | alert with root-cause analysis + 2 buttons |
| 8 | Grafana | http://localhost:3000 | the new run appears on the charts; the alert shows in the table |

Set `scenario` back to `auto` after testing.

## 2. Record the demo (60–90 seconds)

**Tool:** the Windows 11 Snipping Tool records the screen to MP4. Press `Win + Shift + R`, drag over the
browser window, then click Start. Close the other tabs first and hide the bookmarks bar.

**Shot list**

| Time | Show | Say / caption |
|---|---|---|
| 0–10 s | README architecture diagram | "Local ML monitoring agent: Evidently, Postgres, n8n, Ollama, Slack, Grafana. All open source." |
| 10–25 s | n8n workflow canvas → Config node, set `calibration` → **Execute workflow** | "Simulating an imaging-software recalibration in production." |
| 25–40 s | nodes turning green; open the *Run drift check* output (`alert.reasons`) | "Evidently finds PSI > 2 on size features, and accuracy falls from 96% to 80%." |
| 40–55 s | the Slack alert: scroll the root-cause analysis | "Llama 3.1, running locally, explains the likely cause. It never saw the answer." |
| 55–70 s | click **Open Evidently report** → drifted histograms | "Full drift report, one click from the alert." |
| 70–85 s | Grafana dashboard: drift timeline + alerts table | "Drift history, and every alert with its explanation." |

**Don't show on screen:** your `.env` file, the Slack app's *Incoming Webhooks* page (it shows the full
webhook URL), n8n *Settings → Environment* and Grafana data-source settings. The workflow itself shows no
secrets, because it reads them from environment variables.

Your existing n8n on port 5658 has other workflows. Record the new one on port **5678** so only this project appears.

**Size:** GitHub accepts videos up to 10 MB on free accounts. Keep the recording under 90 s at 1080p, or
trim and compress it with the Windows *Clipchamp* app (Export → 720p).

## 3. Clean the n8n JSON and check for secrets

The workflow in `n8n/workflows/drift_alert_workflow.json` contains **no secrets**. The Slack webhook,
Ollama URL and model come from `$env` variables that live only in your git-ignored `.env`.

**Did you change the workflow in the n8n UI and want to save that version?** Download it (⋯ → *Download*),
then sanitize it before replacing the repo copy:

```powershell
python scripts/prepublish_check.py --sanitize "$HOME\Downloads\Model Drift Alert Agent.json"
copy "$HOME\Downloads\Model Drift Alert Agent.json" n8n\workflows\drift_alert_workflow.json
```

The sanitize step removes the instance ID, pinned run data, credential references and version metadata,
and replaces any Slack webhook URL pasted into a node with `{{ $env.SLACK_WEBHOOK_URL }}`.

**Always run the scan before committing:**

```powershell
python scripts/prepublish_check.py
```

It compares every file git would commit against the **actual values in your `.env`**, and against common
key formats (Slack, OpenAI, GitHub, AWS, Google, private keys). It also confirms that `.env` is git-ignored.
Exit code 0 plus "✓ Safe to commit" means you can go ahead.

## 4. Push to GitHub

```powershell
git init
git add .
git status                  # .env and reports/ must NOT be listed
python scripts/prepublish_check.py
git commit -m "Model monitoring + drift alert agent (Evidently, n8n, Ollama, Postgres, Grafana)"
git branch -M main
git remote add origin https://github.com/<you>/model-drift-alert-agent.git
git push -u origin main
```

**Add the video:** on github.com, open `README.md` → ✏️ Edit, then drag the `.mp4` file to just under the
title. GitHub uploads it and inserts a link that plays inline. Commit the change.

Suggested repository description: *Local MLOps monitoring agent: Evidently drift detection, n8n
orchestration, Llama 3.1 root-cause summaries, Slack alerts, Grafana dashboards.*
Suggested topics: `mlops`, `model-monitoring`, `data-drift`, `evidently`, `n8n`, `ollama`, `llm`, `grafana`, `docker`.

**If a secret ever gets pushed:** rotate it first. In Slack: *Incoming Webhooks* → remove the webhook and
create a new one. Then clean the history. Deleting the file in a new commit doesn't remove it from the history.
