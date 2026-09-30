"""Pre-publish safety check: run this before every `git push`.

1. Scans every file that would be committed for secrets:
   - the exact values from your local .env (passwords, Slack webhook, keys)
   - common secret patterns (Slack webhooks/tokens, OpenAI/GitHub/AWS/Google keys, private keys)
2. Optionally sanitizes an n8n workflow you exported from the UI (--sanitize), removing
   instance IDs, pinned run data, credential references and any hard-coded Slack webhook.

Usage (from the project folder, plain Python 3.8+, no packages needed):
    python scripts/prepublish_check.py
    python scripts/prepublish_check.py --sanitize path/to/exported.json
Exit code 1 = something sensitive was found. Fix it before committing.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "reports", "__pycache__", ".venv", "node_modules", "artifacts"}
SKIP_FILES = {".env"}  # the one file that is SUPPOSED to hold secrets (git-ignored)
TEXT_SUFFIXES = {".py", ".json", ".yml", ".yaml", ".md", ".txt", ".sh", ".ps1", ".env",
                 ".example", ".toml", ".cfg", ".ini", ".html", ".js", ".ts", ""}

PATTERNS = {
    "Slack webhook URL": r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}",
    "Slack token": r"xox[abposr]-[A-Za-z0-9-]{10,}",
    "OpenAI/Anthropic-style key": r"\bsk-[A-Za-z0-9_-]{20,}",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    "AWS access key": r"\bAKIA[0-9A-Z]{16}\b",
    "Google API key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "Private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}
# .env values that are placeholders or harmless defaults, never flagged
PLACEHOLDER = re.compile(r"^(change-me.*|replace-with.*|http://(localhost|host\.docker\.internal)[^ ]*|"
                         r"llama3\.1|qwen3:4b|Asia/\w+|\d+|true|false)$", re.I)


def committed_files() -> list[Path]:
    """Files git would commit (tracked + untracked-but-not-ignored); falls back to a walk."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        files = [ROOT / line for line in out.splitlines() if line.strip()]
        if files:
            return [f for f in files if f.is_file()]
    except (OSError, subprocess.CalledProcessError):
        pass
    return [p for p in ROOT.rglob("*")
            if p.is_file() and not (set(p.relative_to(ROOT).parts[:-1]) & SKIP_DIRS)
            and p.name not in SKIP_FILES]


def env_secrets() -> dict[str, str]:
    env = ROOT / ".env"
    if not env.exists():
        return {}
    secrets = {}
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if len(value) >= 6 and not PLACEHOLDER.match(value):
                secrets[key.strip()] = value
    return secrets


def check_gitignore() -> list[str]:
    problems = []
    if (ROOT / ".git").exists():
        r = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
        if r.returncode != 0:
            problems.append(".env is NOT git-ignored. Add '.env' to .gitignore before committing.")
        tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT,
                                 capture_output=True, text=True).stdout.strip()
        if tracked:
            problems.append(".env is already tracked by git. Run: git rm --cached .env")
    return problems


def scan() -> int:
    secrets = env_secrets()
    findings = check_gitignore()
    files = committed_files()
    for path in files:
        if path.name in SKIP_FILES or (path.suffix not in TEXT_SUFFIXES and path.suffix):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = path.relative_to(ROOT)
        for key, value in secrets.items():
            if value in text:
                findings.append(f"{rel}: contains the value of {key} from your .env")
        for name, pattern in PATTERNS.items():
            for m in re.finditer(pattern, text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(f"{rel}:{line}: {name}: {m.group(0)[:24]}…")
    print(f"Scanned {len(files)} files against {len(secrets)} .env secret(s) and "
          f"{len(PATTERNS)} secret patterns.")
    if findings:
        print("\nNOT SAFE TO PUBLISH:")
        for f in findings:
            print("  ✗", f)
        return 1
    print("✓ No secrets found. Safe to commit.")
    return 0


def sanitize(path: Path) -> int:
    wf = json.loads(path.read_text(encoding="utf-8"))
    removed = []
    for key in ("pinData", "staticData", "meta", "shared", "versionId", "activeVersionId",
                "triggerCount", "createdAt", "updatedAt", "isArchived"):
        if key in wf:
            wf.pop(key)
            removed.append(key)
    wf["pinData"] = {}
    wf["active"] = False
    for node in wf.get("nodes", []):
        for key in ("credentials", "webhookId"):
            if key in node:
                node.pop(key)
                removed.append(f"{node['name']}.{key}")
        params = json.dumps(node.get("parameters", {}))
        cleaned = re.sub(PATTERNS["Slack webhook URL"], "={{ $env.SLACK_WEBHOOK_URL }}", params)
        if cleaned != params:
            node["parameters"] = json.loads(cleaned)
            removed.append(f"{node['name']}: hard-coded Slack webhook -> $env.SLACK_WEBHOOK_URL")
    path.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Sanitized {path}")
    for r in removed:
        print("  -", r)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles/pipes may not be UTF-8
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sanitize", type=Path, help="n8n workflow JSON exported from the UI")
    args = ap.parse_args()
    if args.sanitize:
        sanitize(args.sanitize)
    sys.exit(scan())
