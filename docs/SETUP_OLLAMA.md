# Ollama + Llama 3.1 setup

Ollama runs the LLM that writes the root-cause explanation. It runs **natively on Windows** (not in Docker),
so it can use your GPU if you have one. The containers reach it at `http://host.docker.internal:11434`.

## 1. Check what you have

You already have Ollama 0.34.4 with `llama3.2:1b`, `qwen3:4b` and `nomic-embed-text`, but not llama3.1.

```powershell
ollama --version
ollama list
```

If Ollama is ever missing, install it from https://ollama.com/download/windows and it starts in the system tray.

## 2. Pull Llama 3.1 (8B)

```powershell
ollama pull llama3.1
```

- About **4.9 GB** to download. It needs roughly **8 GB of free RAM**, or a GPU with 6 GB+ VRAM.
- Quick check: `ollama run llama3.1 "Explain data drift in one sentence."`

**Don't want the download?** Set `OLLAMA_MODEL=qwen3:4b` in `.env`. The workflow turns off qwen3's "thinking"
mode automatically, so replies stay short. `llama3.2:1b` also works, but its explanations are noticeably weaker.

## 3. Check that Docker can reach it

After `docker compose up -d`:

```powershell
docker compose exec drift-monitor python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags').read()[:300])"
```

You should see JSON that lists your models. If you get *connection refused*:

1. Windows Settings → *System → About → Advanced system settings → Environment Variables*
2. Under **User variables**, add `OLLAMA_HOST` = `0.0.0.0`
3. Quit Ollama from the tray icon, start it again, and re-run the check.

## 4. Point the stack at the model

`.env`:

```
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=llama3.1
```

Apply the change with `docker compose up -d n8n`.

## What the workflow sends

`POST /api/chat` with `stream: false`, `temperature: 0.2`, a 450-token budget, a system prompt that frames
the model as an on-call ML reliability engineer, and a user prompt built from the drift metrics:
drifted features, PSI/KL, target and prediction drift, accuracy/precision/recall, and the list of stable
features. The simulated scenario name is never included. On CPU, expect 30–90 s per explanation.
