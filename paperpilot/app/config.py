from __future__ import annotations

import os
from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("PAPERPILOT_DATA_DIR") or ROOT / "data")
CACHE_DIR = DATA_DIR / "cache"
UPLOAD_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
STATIC_DIR = ROOT / "static"
DOCS_DIR = ROOT / "docs"

for directory in (DATA_DIR, CACHE_DIR, UPLOAD_DIR, PARSED_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

LLM_API_KEY = (
    os.getenv("PAPERPILOT_LLM_API_KEY")
    or os.getenv("OPENAI_API_KEY")
    or ""
).strip()
LLM_BASE_URL = (
    os.getenv("PAPERPILOT_LLM_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or "https://api.openai.com/v1"
).strip()
LLM_MODEL = (os.getenv("PAPERPILOT_LLM_MODEL") or "gpt-4o-mini").strip()
LLM_TIMEOUT = float(os.getenv("PAPERPILOT_LLM_TIMEOUT", "60"))
# Long completions (Agent 简报、全文精读) regularly need more than the default
# socket timeout; a premature ReadTimeout silently degrades them to offline text.
LLM_LONG_TIMEOUT = float(os.getenv("PAPERPILOT_LLM_LONG_TIMEOUT", "180"))
LLM_RETRIES = max(0, min(int(os.getenv("PAPERPILOT_LLM_RETRIES", "1")), 3))
EMBEDDING_MODEL = (os.getenv("PAPERPILOT_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
EMBEDDING_ENABLED = bool(
    LLM_API_KEY
    and os.getenv("PAPERPILOT_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}
)
LLM_ENABLED = bool(LLM_API_KEY)
APP_MODE = "online" if LLM_ENABLED else "offline"
def _parse_routes(raw: str) -> dict[str, str]:
    routes: dict[str, str] = {}
    for part in str(raw or "").split(","):
        if ":" not in part:
            continue
        purpose, model = part.split(":", 1)
        if purpose.strip() and model.strip():
            routes[purpose.strip()] = model.strip()
    return routes


LLM_ROUTES = _parse_routes(os.getenv("PAPERPILOT_LLM_ROUTES", ""))
APP_VERSION = "8.1.2"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ASSISTANT_MAX_MESSAGES = 60
ASSISTANT_LLM_MAX_CALLS = 2
SEMANTIC_AUDIT_MAX_BLOCKS = 20
CLAIM_SUPPORT_OVERLAP = float(os.getenv("PAPERPILOT_CLAIM_SUPPORT_OVERLAP", "0.45"))
CLAIM_PARTIAL_OVERLAP = float(os.getenv("PAPERPILOT_CLAIM_PARTIAL_OVERLAP", "0.20"))
MAX_CLAIMS_PER_BLOCK = 12
CLAIM_LENGTH_RATIO = float(os.getenv("PAPERPILOT_CLAIM_LENGTH_RATIO", "1.35"))

LIBRARY_FILE = DATA_DIR / "library.json"
CONTACT_EMAIL = (os.getenv("PAPERPILOT_CONTACT_EMAIL") or "paperpilot@example.com").strip()
