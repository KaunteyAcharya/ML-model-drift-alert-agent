# Free Slack webhook setup

Incoming webhooks work on Slack's free plan. It takes about 5 minutes.

## 1. Get a workspace and a channel

- Use an existing workspace where you're allowed to add apps, or create a free one at https://slack.com/get-started#/createnew
- Create a channel for alerts, for example **#ml-alerts**.

## 2. Create a Slack app

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**.
2. App name: `Drift Alert Agent`. Pick your workspace → **Create App**.
3. Optional: under *Basic Information → Display Information*, add an icon and description.

## 3. Turn on incoming webhooks

1. In the left menu, open **Incoming Webhooks** and switch **Activate Incoming Webhooks** on.
2. Click **Add New Webhook** at the bottom (older UI: *Add New Webhook to Workspace*).
3. Choose **#ml-alerts** → **Allow**.
4. Copy the webhook URL. It looks like `https://hooks.slack.com/services/T…/B…/…`

Treat the URL like a password: anyone who has it can post to your channel. It belongs in `.env`, which is git-ignored.

## 4. Test it from PowerShell

```powershell
$url = "https://hooks.slack.com/services/XXX/YYY/ZZZ"
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Body '{"text":"Hello from the drift alert agent :wave:"}'
```

A reply of `ok` means the message arrived in the channel.

## 5. Connect it to the stack

In `.env`:

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
```

Then recreate n8n so it picks up the new value. Use `up -d`, not `restart`:

```powershell
docker compose up -d n8n
```

## 6. Trigger a real alert

In n8n, open **Model Drift Alert Agent** → **Config**, set `scenario` to `calibration` → **Execute workflow**.
The message contains the severity, drifted-feature count, max PSI, accuracy change, why the alert fired, the
LLM root-cause analysis, the top drifting features, and buttons that open the Evidently report and Grafana.
Set `scenario` back to `auto` afterwards.

The report and Grafana links point at `localhost`, so they open on the PC running the stack. To share them,
put the stack behind a tunnel or a server, then update `REPORT_BASE_URL` and `GRAFANA_PUBLIC_URL`.
