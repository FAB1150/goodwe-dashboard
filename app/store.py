"""Persistence in /data: configuration (inverter IP) and change log."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

_DEFAULT_CONFIG = {"host": os.environ.get("INVERTER_HOST", ""), "port": int(os.environ.get("INVERTER_PORT", "8899"))}


class Store:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.dir / "config.json"
        self.db_path = self.dir / "changes.sqlite"
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    setting TEXT NOT NULL,
                    label TEXT NOT NULL,
                    before TEXT,
                    requested TEXT,
                    reread TEXT,
                    outcome TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    frames TEXT
                )"""
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # --- configuration ----------------------------------------------------
    def get_config(self) -> dict[str, Any]:
        cfg = dict(_DEFAULT_CONFIG)
        if self.config_path.exists():
            try:
                cfg.update(json.loads(self.config_path.read_text()))
            except (OSError, ValueError):
                pass
        return cfg

    def set_config(self, **values: Any) -> dict[str, Any]:
        with self._lock:
            cfg = self.get_config()
            cfg.update(values)
            tmp = self.config_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(cfg, indent=2))
            tmp.replace(self.config_path)
            return cfg

    # --- change log -------------------------------------------------------
    def log_change(
        self,
        setting: str,
        label: str,
        before: Any,
        requested: Any,
        reread: Any,
        outcome: str,
        dry_run: bool,
        frames: list[dict],
    ) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO changes (at, setting, label, before, requested, reread, outcome, dry_run, frames)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    setting,
                    label,
                    json.dumps(before),
                    json.dumps(requested),
                    json.dumps(reread),
                    outcome,
                    1 if dry_run else 0,
                    json.dumps(frames),
                ),
            )

    def list_changes(self, limit: int = 200) -> list[dict]:
        with self._db() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("before", "requested", "reread", "frames"):
                try:
                    d[k] = json.loads(d[k]) if d[k] is not None else None
                except ValueError:
                    pass
            d["dry_run"] = bool(d["dry_run"])
            out.append(d)
        return out
