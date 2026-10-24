from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets" / "cloudflare.env"
SHOWCASE = ROOT / "showcase"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def run(command: list[str], env: dict[str, str]) -> str:
    print("$", " ".join(command[:4]), "...")
    prefix = ["cmd.exe", "/c"] if os.name == "nt" else []
    result = subprocess.run(
        prefix + command,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    output = (result.stdout or "") + (result.stderr or "")
    print(output[-5000:])
    if result.returncode != 0:
        raise RuntimeError(f"command failed with code {result.returncode}")
    return output


def main() -> None:
    if not SECRETS.exists():
        raise SystemExit(f"Missing {SECRETS}; create it from .secrets/cloudflare.env.example")
    values = load_env(SECRETS)
    token = values.get("CLOUDFLARE_API_TOKEN", "")
    account = values.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not token or not account:
        raise SystemExit("CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID must be set")

    env = os.environ.copy()
    env["CLOUDFLARE_API_TOKEN"] = token
    env["CLOUDFLARE_ACCOUNT_ID"] = account
    env["CI"] = "1"

    project = "paperpilot-showcase"
    try:
        run(
            [
                "npx", "--yes", "wrangler", "pages", "project", "create",
                project, "--production-branch", "main",
            ],
            env,
        )
    except Exception as exc:
        print("Pages project may already exist:", str(exc)[:200])
    output = run(
        [
            "npx", "--yes", "wrangler", "pages", "deploy", "showcase",
            "--project-name", project,
            "--branch", "main",
            "--commit-dirty=true",
        ],
        env,
    )
    matches = re.findall(r"https://[a-z0-9-]+\.pages\.dev", output)
    url = matches[-1] if matches else f"https://{project}.pages.dev"
    print("Cloudflare Pages:", url)


if __name__ == "__main__":
    main()
