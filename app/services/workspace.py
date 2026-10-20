from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.config import Settings


def workspace_for(settings: Settings, user_id: int) -> Path:
    path = settings.data_dir / "workspaces" / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "inbox").mkdir(exist_ok=True)
    (path / "briefings").mkdir(exist_ok=True)
    (path / "corpus").mkdir(exist_ok=True)
    return path


def sync_corpus(settings: Settings, workspace: Path) -> None:
    source = settings.corpus_dir
    if not source.exists():
        return
    target = workspace / "corpus"
    for item in source.glob("*.md"):
        dest = target / item.name
        if not dest.exists():
            shutil.copy(item, dest)


def write_preferences(workspace: Path, prefs: dict) -> None:
    payload = {
        "topics": prefs.get("topics") or [],
        "keywords": prefs.get("keywords") or [],
        "language": prefs.get("language") or "zh",
    }
    (workspace / "preferences.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
