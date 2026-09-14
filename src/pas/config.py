import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: str | None = None) -> dict:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path or os.getenv("PAS_CONFIG") or ROOT / "config" / "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def db_dsn() -> str:
    load_dotenv(ROOT / ".env")
    if dsn := os.getenv("PAS_DB_DSN"):
        return dsn
    user = os.getenv("POSTGRES_USER", "pas")
    password = os.getenv("POSTGRES_PASSWORD", "pas_local_dev")
    host = os.getenv("PAS_DB_HOST", "localhost")
    port = os.getenv("PAS_DB_PORT", "5433")
    name = os.getenv("POSTGRES_DB", "pas")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"
