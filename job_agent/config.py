"""Loads config.yaml. Relative paths are resolved against the config file's folder."""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

PATH_KEYS = ["skills_dir", "exports_dir", "profile", "preferences", "scoring", "data_dir", "env_file", "inbox_dir"]


@dataclass
class Config:
    skills_dir: Path
    exports_dir: Path
    profile: Path
    preferences: Path
    scoring: Path
    data_dir: Path
    env_file: Path | None = None
    inbox_dir: Path | None = None   # jobs shared by hand to the Telegram bot, see worker/
    max_age_days: int = 14          # older posts are probably filled already
    read_min_score: int = 65        # first-round score needed to open the job page
    read_limit: int = 20            # most job pages opened per run
    digest_min_score: int = 70
    digest_size: int = 10


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = path.resolve().parent
    for key in PATH_KEYS:
        if raw.get(key):
            raw[key] = (base / raw[key]).resolve()
    config = Config(**raw)
    if config.env_file and config.env_file.exists():
        load_env(config.env_file)
    return config


def load_env(path: Path) -> None:
    """Reads KEY=value lines into the environment, without overriding variables already set."""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
