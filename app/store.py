"""Persistenza in /data: configurazione (IP dell'inverter) e registro delle modifiche."""

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
        self.db_path = self.dir / "registro.sqlite"
        self._lock = threading.Lock()
        with self._db() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS modifiche (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    quando TEXT NOT NULL,
                    impostazione TEXT NOT NULL,
                    etichetta TEXT NOT NULL,
                    prima TEXT,
                    richiesto TEXT,
                    riletto TEXT,
                    esito TEXT NOT NULL,
                    prova_a_secco INTEGER NOT NULL,
                    frame TEXT
                )"""
            )

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # --- configurazione ---------------------------------------------------
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

    # --- registro modifiche ----------------------------------------------
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
                "INSERT INTO modifiche (quando, impostazione, etichetta, prima, richiesto, riletto, esito, prova_a_secco, frame)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    setting,
                    label,
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(requested, ensure_ascii=False),
                    json.dumps(reread, ensure_ascii=False),
                    outcome,
                    1 if dry_run else 0,
                    json.dumps(frames, ensure_ascii=False),
                ),
            )

    def list_changes(self, limit: int = 200) -> list[dict]:
        with self._db() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM modifiche ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("prima", "richiesto", "riletto", "frame"):
                try:
                    d[k] = json.loads(d[k]) if d[k] is not None else None
                except ValueError:
                    pass
            d["prova_a_secco"] = bool(d["prova_a_secco"])
            out.append(d)
        return out
