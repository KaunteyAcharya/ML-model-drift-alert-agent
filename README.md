# Model Monitoring + Drift Alert Agent

A local, fully open-source MLOps monitoring stack. A scheduled n8n workflow scores a new batch of
"production" traffic, logs every prediction to Postgres, measures data/prediction/target drift and model
quality with **Evidently AI**, and when a threshold is crossed asks a **local LLM (Ollama)** to explain
the likely root cause in plain English. That explanation is posted to **Slack** with a link to the full
Evidently report and a **Grafana** dashboard.

Everything runs on your machine in Docker. No paid services and no data leaves your PC (except the Slack message).

```
 n8n schedule ──► drift-monitor (FastAPI + Evidently) ──► Postgres ◄── Grafana dashboard
       │                 │ JSON report + HTML report
       ▼                 ▼
  threshold? ── yes ──► Ollama (llama3.1, local) ──► Slack alert ──► summary saved to Postgres
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full design, metrics and a portfolio write-up.

---

## Project structure

```
├── docker-compose.yml          postgres, drift-monitor, n8n, grafana (+ optional ollama)
├── .env.example                copy to .env: passwords, Slack webhook, Ollama model
├── monitor/                    the drift service (its own container)
│   ├── Dockerfile              trains the model at build time → frozen baseline
│   └── app/
│       ├── train.py            RandomForest on UCI Breast Cancer Wisconsin + reference set
│       ├── simulate.py         production traffic with 4 drift scenarios + an "auto" storyline
│       ├── drift.py            Evidently: K-S/PSI/KL per feature, dataset/prediction/target drift, quality
│       ├── pipeline.py         one monitoring cycle: simulate → score → log → analyse → report
│       ├── db.py               Postgres schema + logging (predictions, drift_runs, feature_drift)
│       ├── api.py              POST /run for n8n, serves /reports/*.html
│       └── cli.py              run it by hand; backfill history for Grafana
├── postgres/init/01-init.sh    creates n8n + monitoring DBs and a read-only Grafana role
├── grafana/                    provisioned datasource + "Model Drift Overview" dashboard
├── n8n/workflows/drift_alert_workflow.json   import-ready workflow
├── reports/                    Evidently HTML + JSON reports appear here
├── scripts/prepublish_check.py   secret scan + n8n export sanitizer. Run before every push
└── docs/                       SETUP_OLLAMA.md, SETUP_SLACK.md, ARCHITECTURE.md, PUBLISHING.md
```

---

## Quick start (Windows, PowerShell)

Prerequisites: Docker Desktop running, Ollama installed. You have both already.

**1. Configure.** From this folder:

```powershell
copy .env.example .env
notepad .env
```

Change the four database passwords, `GRAFANA_ADMIN_PASSWORD` and `N8N_ENCRYPTION_KEY`
(the file shows a one-liner to generate a key). Use letters and digits only. Leave `SLACK_WEBHOOK_URL` empty for now.

**2. Get the LLM.** See [docs/SETUP_OLLAMA.md](docs/SETUP_OLLAMA.md). In short, `ollama pull llama3.1`,
or set `OLLAMA_MODEL=qwen3:4b` in `.env` to use a model you already have.

**3. Start the stack.** The first build takes a few minutes: it downloads the images and trains the model.

```powershell
docker compose up -d --build
docker compose ps          # wait until everything shows "healthy" / "running"
```

**4. Set up n8n.** Open http://localhost:5678 and create the owner account (local only). Then import and publish the workflow:

```powershell
docker compose exec n8n n8n import:workflow --input=/workflows/drift_alert_workflow.json
docker compose exec n8n n8n publish:workflow --id=driftAlertAgent1
docker compose restart n8n
```

(Or in the UI: *Create workflow → ⋯ → Import from file*, then click **Publish**.)

**5. Seed some history** so the dashboard isn't empty (12 simulated runs, one every 2 hours):

```powershell
docker compose exec drift-monitor python -m app.cli backfill --steps 12 --interval-hours 2
```

**6. Watch it work.**
- **n8n** (http://localhost:5678): open *Model Drift Alert Agent* → **Execute workflow**. The schedule then runs every 15 minutes.
- **Grafana** (http://localhost:3000, user `admin`): the *Model Drift Overview* dashboard opens on the home page.
- **Reports** (http://localhost:8010/runs): recent runs with links to each Evidently HTML report.

**7. Add Slack** when ready: follow [docs/SETUP_SLACK.md](docs/SETUP_SLACK.md), paste the URL into `.env`, then
`docker compose up -d n8n`. Use `up -d`, not `restart`: a restart doesn't re-read `.env`.

**8. Before publishing:** run `python scripts/prepublish_check.py`. See [docs/PUBLISHING.md](docs/PUBLISHING.md)
for the verification checklist, a demo-video shot list and the safe GitHub push steps.

---

## Trying the drift scenarios

The workflow's **Config** node sets `scenario` (default `auto`) and the alert thresholds.

| scenario | what it simulates | what the monitor sees |
|---|---|---|
| `none` | healthy traffic | no alert |
| `calibration` | imaging software update traces nuclei ~25% larger; labels unchanged | size + texture features drift, predictions skew malignant, **accuracy drops ~96% → ~80%** (critical) |
| `population` | new referral clinic sends far more malignant cases | most features drift **and** the target rate drifts, yet the model stays accurate (warning) |
| `data_quality` | upstream bug: `mean_area` in wrong unit, `worst_concavity` often null | one feature with huge PSI + missing values (critical) |
| `auto` | cycles: 3 healthy → 5 gradually worsening calibration → 2 population → 2 data quality | a realistic timeline in Grafana |

The scenario name is **never sent to the LLM**. It has to infer the cause from the metrics. The ground truth
is stored in `drift_runs.sim_scenario`, so you can score how often the explanation was right.

Run a cycle by hand (prints the JSON report):

```powershell
docker compose exec drift-monitor python -m app.cli run --scenario calibration --severity 0.8 --summary
```

---

## Design note: why HTTP instead of n8n's Execute Command node

Execute Command is disabled by default since n8n 2.0 for security reasons, and the official n8n image has no
Python or package manager. So the drift script runs in its own container, and n8n calls it over HTTP
(`POST http://drift-monitor:8000/run`). The ML dependencies stay out of the orchestrator, each piece can be
scaled or replaced on its own, and the same code still runs from the command line (`app.cli`).

---

## Troubleshooting

| symptom | fix |
|---|---|
| Slack shows *"Root-cause summary unavailable: Ollama error"* | Is Ollama running (tray icon)? Is the model pulled (`ollama list`)? Does `OLLAMA_MODEL` in `.env` match? Then `docker compose up -d n8n`. |
| Ollama unreachable from Docker (`ECONNREFUSED host.docker.internal:11434`) | Set a Windows user environment variable `OLLAMA_HOST=0.0.0.0`, quit Ollama from the tray and start it again. |
| Ollama call times out | llama3.1 (8B) on CPU can need 30–90 s, and the first call also loads the model. The node allows 5 minutes. For faster replies, use `qwen3:4b`. |
| Port already in use | Change the left-hand port in `docker-compose.yml` (for example `"5679:5678"`). |
| Start completely fresh | `docker compose down -v` deletes all volumes: database, n8n workflows and Grafana state. |
| Disk filling up | Each HTML report is ~6 MB; `REPORT_RETENTION` keeps the newest 50. |

Pinned versions: n8n 2.40.7, Grafana 13.2.3, Postgres 17, Python 3.11, Evidently 0.7.23, scikit-learn 1.8.0.
The drift pipeline, Postgres logging, every Grafana query and all three workflow paths (no drift, alert, pipeline failure)
were tested end to end on n8n 2.40.7, with Ollama and Slack mocked.
