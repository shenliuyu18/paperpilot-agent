from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import requests
import truststore

truststore.inject_into_ssl()

# A Windows console opened with the locale codec (GBK here) must not abort a deploy while printing
# runtime text -- API error bodies, repo paths, Chinese titles.
if getattr(sys.stdout, "errors", "") == "strict":
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets" / "github.env"
API = "https://api.github.com"
sys.path.insert(0, str(ROOT / "paperpilot" / "scripts"))

from package_project import collect as collect_package_files  # noqa: E402


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def collect() -> list[tuple[str, Path]]:
    """Deploy exactly the delivery-package allowlist, never the whole workspace tree.

    GitHub is pushed through the Git Data API rather than a working Git checkout,
    so .gitignore cannot protect runtime state here. Reusing the package allowlist
    keeps the repository and PaperPilot_Project.zip structurally identical.
    """
    files = [(path.relative_to(ROOT).as_posix(), path) for path in collect_package_files()]
    unique = []
    seen = set()
    for rel, path in files:
        if rel not in seen:
            seen.add(rel)
            unique.append((rel, path))
    return sorted(unique)


def main() -> None:
    if not SECRETS.exists():
        raise SystemExit(f"Missing {SECRETS}; create it from .secrets/github.env.example")
    env = load_env(SECRETS)
    token = env.get("GITHUB_TOKEN", "")
    owner = env.get("GITHUB_OWNER", "shenliuyu18")
    repo = env.get("GITHUB_REPO", "paperpilot-agent")
    if not token:
        raise SystemExit("GITHUB_TOKEN is empty")

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "PaperPilot-Deploy",
        }
    )

    def api(method: str, path: str, **kwargs):
        last = None
        for attempt in range(4):
            response = session.request(method, API + path, timeout=90, **kwargs)
            if response.status_code < 400:
                return response.json() if response.content else {}
            last = response
            if response.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            break
        raise RuntimeError(
            f"GitHub API {method} {path} -> {last.status_code}: {last.text[:500]}"
        )

    me = api("GET", "/user")
    print(f"GitHub user: {me.get('login')}")

    ref = api("GET", f"/repos/{owner}/{repo}/git/ref/heads/main")
    base_commit = ref["object"]["sha"]
    base = api("GET", f"/repos/{owner}/{repo}/git/commits/{base_commit}")
    base_tree = base["tree"]["sha"]

    files = collect()
    print(f"main files: {len(files)}")
    tree = []
    for index, (rel, path) in enumerate(files, start=1):
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        blob = api("POST", f"/repos/{owner}/{repo}/git/blobs", json={"content": data, "encoding": "base64"})
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if index % 10 == 0 or index == len(files):
            print(f"  blobs {index}/{len(files)}")

    main_tree = api(
        "POST",
        f"/repos/{owner}/{repo}/git/trees",
        json={"tree": tree},
    )
    commit = api(
        "POST",
        f"/repos/{owner}/{repo}/git/commits",
        json={
            "message": "Deploy PaperPilot source, demo video, and static showcase",
            "tree": main_tree["sha"],
            "parents": [base_commit],
        },
    )
    api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/main", json={"sha": commit["sha"], "force": True})
    print("main updated:", commit["sha"])

    # GitHub Pages branch: full static showcase at the branch root.
    pages_files = [
        ("index.html", ROOT / "showcase" / "index.html"),
        ("paperpilot_demo.mp4", ROOT / "showcase" / "paperpilot_demo.mp4"),
        ("cover.jpg", ROOT / "showcase" / "cover.jpg"),
    ]
    pages_tree = []
    for rel, path in pages_files:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        blob = api("POST", f"/repos/{owner}/{repo}/git/blobs", json={"content": data, "encoding": "base64"})
        pages_tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree_obj = api("POST", f"/repos/{owner}/{repo}/git/trees", json={"tree": pages_tree})
    pages_commit = api(
        "POST",
        f"/repos/{owner}/{repo}/git/commits",
        json={
            "message": "Deploy GitHub Pages showcase",
            "tree": tree_obj["sha"],
            "parents": [commit["sha"]],
        },
    )
    try:
        api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/gh-pages", json={"sha": pages_commit["sha"], "force": True})
        print("gh-pages updated")
    except RuntimeError:
        api("POST", f"/repos/{owner}/{repo}/git/refs", json={"ref": "refs/heads/gh-pages", "sha": pages_commit["sha"]})
        print("gh-pages created")

    pages_url = f"https://{owner}.github.io/{repo}/"
    try:
        current = session.get(f"{API}/repos/{owner}/{repo}/pages", timeout=30)
        if current.status_code == 404:
            api(
                "POST",
                f"/repos/{owner}/{repo}/pages",
                json={"source": {"branch": "gh-pages", "path": "/"}},
            )
        elif current.status_code == 200:
            api(
                "PUT",
                f"/repos/{owner}/{repo}/pages",
                json={"source": {"branch": "gh-pages", "path": "/"}},
            )
        print("Pages configured:", pages_url)
        api("POST", f"/repos/{owner}/{repo}/pages/builds")
        for _ in range(18):
            time.sleep(5)
            info = api("GET", f"/repos/{owner}/{repo}/pages")
            status = info.get("status")
            print("  Pages status:", status, info.get("html_url", ""))
            if status == "built":
                break
    except Exception as exc:
        print("Pages API note:", str(exc)[:300])
        print("If needed, enable Pages manually: Settings -> Pages -> Source: gh-pages / root")

    print("GitHub Pages:", pages_url)
    print("Repository:", f"https://github.com/{owner}/{repo}")


if __name__ == "__main__":
    main()
