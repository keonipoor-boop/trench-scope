"""SQLite storage. Coins keep a few indexed columns + a JSON `data` blob, so new
fields from new sources need no migration."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY,
  name TEXT, symbol TEXT,
  created_ts INTEGER,
  ath_mcap REAL DEFAULT 0,
  ts_100k INTEGER,
  is_runner INTEGER DEFAULT 0,
  is_real INTEGER,
  primary_catalyst TEXT,
  themes TEXT DEFAULT '[]',
  cheap_ts INTEGER DEFAULT 0,
  ohlcv_ts INTEGER DEFAULT 0,
  deep_ts INTEGER DEFAULT 0,
  updated_ts INTEGER,
  data TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_coins_created ON coins(created_ts);
CREATE INDEX IF NOT EXISTS ix_coins_runner ON coins(is_runner, ath_mcap);

CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mint TEXT, ts INTEGER, mcap REAL, holders INTEGER, volume_24h REAL, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_snap_mint ON snapshots(mint, ts);

CREATE TABLE IF NOT EXISTS theses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT, ts INTEGER, leader TEXT, meta_shift INTEGER DEFAULT 0,
  text TEXT, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_theses_kind ON theses(kind, ts);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL, agent TEXT, level TEXT, msg TEXT, data TEXT
);

CREATE TABLE IF NOT EXISTS source_status (
  name TEXT PRIMARY KEY, agent TEXT, ok INTEGER, last_ok_ts INTEGER, last_err_ts INTEGER,
  last_error TEXT, calls INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, latency_ms REAL, note TEXT
);

CREATE TABLE IF NOT EXISTS agents (
  name TEXT PRIMARY KEY, status TEXT, detail TEXT, last_ts INTEGER, runs INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""

COLUMNS = ("name", "symbol", "created_ts", "ath_mcap", "ts_100k", "is_runner", "is_real",
           "primary_catalyst", "cheap_ts", "ohlcv_ts", "deep_ts")


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args=()) -> int:
        with self._lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # ---- coins -------------------------------------------------------------
    @staticmethod
    def row_to_coin(r: sqlite3.Row) -> dict:
        c = json.loads(r["data"] or "{}")
        c["mint"] = r["mint"]
        for k in COLUMNS:
            if r[k] is not None:
                c[k] = r[k]
        c["themes"] = json.loads(r["themes"] or "[]")
        c["is_runner"] = bool(r["is_runner"])
        c["is_real"] = None if r["is_real"] is None else bool(r["is_real"])
        return c

    def light_coins(self, since: int) -> list[dict]:
        """Columns only (fast). Runners also get their description for emerging-word detection."""
        rows = self.q("""SELECT mint, name, symbol, created_ts, ath_mcap, ts_100k, is_runner, is_real,
                         primary_catalyst, themes,
                         CASE WHEN is_runner=1 THEN json_extract(data,'$.description') END AS description,
                         CASE WHEN is_runner=1 THEN json_extract(data,'$.time_to_100k_min') END AS ttt
                         FROM coins WHERE created_ts >= ?""", (since,))
        out = []
        for r in rows:
            d = dict(r)
            d["themes"] = json.loads(d["themes"] or "[]")
            d["is_runner"] = bool(d["is_runner"])
            d["is_real"] = None if d["is_real"] is None else bool(d["is_real"])
            d["time_to_100k_min"] = d.pop("ttt")
            out.append(d)
        return out

    def get_coin(self, mint: str) -> dict | None:
        rows = self.q("SELECT * FROM coins WHERE mint=?", (mint,))
        return self.row_to_coin(rows[0]) if rows else None

    def coins(self, where: str = "1=1", args=(), order: str = "created_ts DESC", limit: int | None = None) -> list[dict]:
        sql = f"SELECT * FROM coins WHERE {where} ORDER BY {order}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self.row_to_coin(r) for r in self.q(sql, args)]

    def upsert_many(self, coins: list[dict], merge: bool = True):
        with self._lock:
            for c in coins:
                self.upsert_coin(c, merge=merge, commit=False)
            self.conn.commit()

    def upsert_coin(self, coin: dict, merge: bool = True, commit: bool = True):
        """Insert or merge a coin dict. Missing/None values never overwrite stored ones."""
        mint = coin["mint"]
        with self._lock:
            existing = self.get_coin(mint) if merge else None
            c = dict(existing or {})
            for k, v in coin.items():
                if v is None:
                    continue
                c[k] = v
            c["ath_mcap"] = max(float(c.get("ath_mcap") or 0), float((existing or {}).get("ath_mcap") or 0))
            olds = [x for x in ((existing or {}).get("created_ts"), coin.get("created_ts")) if x]
            if olds:
                c["created_ts"] = min(olds)  # sources disagree; launch = earliest sighting
            cols = {k: c.get(k) for k in COLUMNS}
            cols["is_runner"] = int(bool(c.get("is_runner")))
            if c.get("is_real") is not None:
                cols["is_real"] = int(bool(c["is_real"]))
            themes = json.dumps(c.get("themes") or [])
            blob = {k: v for k, v in c.items() if k not in COLUMNS and k not in ("mint", "themes")}
            self.conn.execute(
                f"""INSERT INTO coins (mint, {', '.join(COLUMNS)}, themes, updated_ts, data)
                    VALUES (?, {', '.join('?' for _ in COLUMNS)}, ?, ?, ?)
                    ON CONFLICT(mint) DO UPDATE SET
                    {', '.join(f'{k}=excluded.{k}' for k in COLUMNS)},
                    themes=excluded.themes, updated_ts=excluded.updated_ts, data=excluded.data""",
                (mint, *[cols[k] for k in COLUMNS], themes, int(time.time()), json.dumps(blob, default=str)),
            )
            if commit:
                self.conn.commit()

    # ---- kv ----------------------------------------------------------------
    def kv_get(self, k: str, default=None):
        r = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r[0]["v"]) if r else default

    def kv_set(self, k: str, v):
        self.x("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


_DB: DB | None = None


def get_db() -> DB:
    global _DB
    if _DB is None:
        from core.config import get_config
        _DB = DB(get_config().db_path)
    return _DB


def reset_db():
    global _DB
    _DB = None
