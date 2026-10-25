from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

# A Windows console opened with the locale codec (GBK here) must not abort a deploy while printing
# runtime text -- API error bodies, repo paths, Chinese titles.
if getattr(sys.stdout, "errors", "") == "strict":
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets" / "modelscope.env"
STAGE = ROOT / f".modelscope_space_build_{int(time.time())}"
API = "https://modelscope.cn/openapi/v1"
sys.path.insert(0, str(ROOT / "paperpilot" / "scripts"))

from package_project import collect as collect_package_files  # noqa: E402
GIT_HOST = "www.modelscope.cn"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def redact(text: str, token: str) -> str:
    return text.replace(token, "<token>") if token else text


def main() -> None:
    if not SECRETS.exists():
        raise SystemExit(f"Missing {SECRETS}; create it from .secrets/modelscope.env.example")
    values = load_env(SECRETS)
    token = values.get("MODELSCOPE_API_KEY", "")
    if not token:
        raise SystemExit("MODELSCOPE_API_KEY is empty")
    owner = values.get("MODELSCOPE_OWNER", "").strip()
    name = values.get("MODELSCOPE_STUDIO_NAME", "paperpilot").strip() or "paperpilot"

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "PaperPilot-Deploy",
        }
    )

    def api(method: str, path: str, **kwargs):
        response = session.request(method, API + path, timeout=90, **kwargs)
        try:
            payload = response.json()
        except Exception:
            payload = {"raw": response.text[:1000]}
        if response.status_code >= 400:
            raise RuntimeError(f"ModelScope API {method} {path} -> {response.status_code}: {payload}")
        if isinstance(payload, dict) and payload.get("success") is False:
            raise RuntimeError(f"ModelScope API {method} {path} -> {payload}")
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    user = api("GET", "/users/me")
    if not owner:
        owner = user.get("name") or user.get("username") or user.get("user_name")
    if not owner:
        raise SystemExit(f"Cannot determine ModelScope user: {user}")
    print("ModelScope owner:", owner)

    existing = None
    try:
        existing = api("GET", f"/studios/{owner}/{name}")
        print("Studio already exists:", owner, name)
    except RuntimeError as exc:
        print("Studio not found (will create).", str(exc)[:200])

    hardware = None
    try:
        hardware_data = api("GET", "/studios/hardware?sdk_type=docker")
        if isinstance(hardware_data, dict):
            hardware_data = hardware_data.get("items") or hardware_data.get("list") or hardware_data.get("hardware") or []
        if isinstance(hardware_data, list) and hardware_data:
            print("Available hardware:")
            for item in hardware_data[:20]:
                print("  ", item.get("name"), item.get("resource_type"), item.get("price", ""))
            free = next(
                (
                    item for item in hardware_data
                    if item.get("resource_type") != "paid"
                    and "cpu" in str(item.get("name", "")).lower()
                ),
                None,
            )
            free = free or next((item for item in hardware_data if item.get("resource_type") != "paid"), None)
            if free:
                hardware = free.get("name")
    except Exception as exc:
        print("Hardware query note:", str(exc)[:200])

    if not existing:
        payload = {
            "owner": owner,
            "repo_name": name,
            "sdk_type": "docker",
            "visibility": "public",
            "display_name": "PaperPilot",
        }
        if hardware:
            payload["hardware"] = hardware
        try:
            created = api("POST", "/studios", json=payload)
            print("Studio created:", created.get("id") if isinstance(created, dict) else created)
        except RuntimeError as exc:
            print("Create Studio failed:", exc)
            print("Docker Studio requires Alibaba Cloud binding and real-name verification.")
            raise SystemExit(1)

    STAGE.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "modelscope_space" / "Dockerfile", STAGE / "Dockerfile")
    shutil.copy2(ROOT / "modelscope_space" / "README.md", STAGE / "README.md")
    # The Studio is pushed as a fresh Git repo. Copy the delivery allowlist rather
    # than the working tree so caches, uploads and runtime JSON never enter Docker.
    for path in collect_package_files():
        relative = path.relative_to(ROOT)
        if relative.parts[0] != "paperpilot":
            continue
        target = STAGE / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    for relative in ("data/uploads", "data/demo_pdfs", "docs"):
        path = STAGE / "paperpilot" / relative
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    git_base = ["git", "-c", "user.name=PaperPilot", "-c", "user.email=paperpilot@example.com"]

    def run_git(args: list[str]) -> subprocess.CompletedProcess:
        result = subprocess.run(
            git_base + args,
            cwd=str(STAGE),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            print(redact((result.stdout or "") + (result.stderr or ""), token)[-3000:])
        return result

    if not (STAGE / ".git").exists():
        run_git(["init"])
        run_git(["checkout", "-b", "master"])
    run_git(["add", "-A"])
    run_git(["commit", "-m", "Deploy PaperPilot to ModelScope Studio"])
    remote = f"https://oauth2:{urllib.parse.quote(token, safe='')}@{GIT_HOST}/studios/{owner}/{name}.git"
    run_git(["remote", "remove", "modelscope"])
    run_git(["remote", "add", "modelscope", remote])
    fetched = run_git(["fetch", "modelscope", "master"])
    if fetched.returncode == 0:
        merged = run_git(
            ["merge", "modelscope/master", "--allow-unrelated-histories", "-m", "Merge remote Studio README"]
        )
        if merged.returncode != 0:
            run_git(["checkout", "--ours", "."])
            run_git(["add", "-A"])
            run_git(["commit", "-m", "Resolve conflicts, keep local version"])
    pushed = run_git(["push", "-u", "modelscope", "master"])
    if pushed.returncode != 0:
        raise SystemExit("Git push to ModelScope Studio failed; see redacted output above.")
    print("Code pushed to ModelScope Studio.")

    try:
        api("POST", f"/studios/{owner}/{name}/deploy")
        print("Deploy triggered.")
    except Exception as exc:
        print("Deploy trigger note:", str(exc)[:400])

    for _ in range(30):
        time.sleep(10)
        try:
            info = api("GET", f"/studios/{owner}/{name}")
            print("  status:", info.get("status") or info.get("deploy_status") or info.get("state"))
        except Exception as exc:
            print("  status note:", str(exc)[:160])
    for log_type in ("build", "run"):
        try:
            response = session.get(f"{API}/studios/{owner}/{name}/logs/{log_type}", timeout=60)
            print(f"--- {log_type} logs (tail) ---")
            print(response.text[-2500:])
        except Exception as exc:
            print(f"{log_type} logs note:", str(exc)[:200])

    print("Studio URL:", f"https://modelscope.cn/studios/{owner}/{name}")
    print("Note: Docker Studio requires Alibaba Cloud binding + real-name verification.")


if __name__ == "__main__":
    main()
