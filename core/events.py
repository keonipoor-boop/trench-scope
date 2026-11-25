"""Event log ("THE TRACE"), agent status ("AGENT STRIP") and source health ("SENSORY INPUT")."""
from __future__ import annotations

import json
import time
import traceback

from core.db import get_db

AGENTS = ["SCOUT", "HOLDERS", "KOLS", "FEES", "CATALYST", "THESIS"]


def log(agent: str, msg: str, level: str = "info", **data):
    agent = agent.upper()
    try:
        get_db().x("INSERT INTO events(ts,agent,level,msg,data) VALUES(?,?,?,?,?)",
                   (time.time(), agent, level, msg, json.dumps(data, default=str) if data else None))
    except Exception:
        pass
    stamp = time.strftime("%H:%M:%S")
    print(f"{stamp} [{agent:<8}] {'!' if level == 'error' else ' '} {msg}", flush=True)


def agent_status(name: str, status: str, detail: str = ""):
    """status: idle | running | ok | warn | error"""
    db = get_db()
    db.x("""INSERT INTO agents(name,status,detail,last_ts,runs) VALUES(?,?,?,?,1)
            ON CONFLICT(name) DO UPDATE SET status=excluded.status, detail=excluded.detail,
            last_ts=excluded.last_ts, runs=agents.runs + (CASE WHEN excluded.status='running' THEN 1 ELSE 0 END)""",
         (name.upper(), status, detail[:300], int(time.time())))


def source_result(name: str, agent: str, ok: bool, latency_ms: float | None = None,
                  error: str | None = None, note: str | None = None):
    now = int(time.time())
    db = get_db()
    db.x("""INSERT INTO source_status(name,agent,ok,last_ok_ts,last_err_ts,last_error,calls,errors,latency_ms,note)
            VALUES(?,?,?,?,?,?,1,?,?,?)
            ON CONFLICT(name) DO UPDATE SET agent=excluded.agent, ok=excluded.ok,
              last_ok_ts=COALESCE(excluded.last_ok_ts, source_status.last_ok_ts),
              last_err_ts=COALESCE(excluded.last_err_ts, source_status.last_err_ts),
              last_error=COALESCE(excluded.last_error, source_status.last_error),
              calls=source_status.calls+1, errors=source_status.errors+excluded.errors,
              latency_ms=COALESCE(excluded.latency_ms, source_status.latency_ms),
              note=COALESCE(excluded.note, source_status.note)""",
         (name, agent, int(ok), now if ok else None, None if ok else now, (error or "")[:300] or None,
          0 if ok else 1, latency_ms, note))


class AgentRun:
    """with AgentRun("FEES", "sampling txs"): ...  -> sets running/ok/error + logs failures."""

    def __init__(self, agent: str, detail: str = ""):
        self.agent, self.detail, self.result = agent.upper(), detail, ""

    def __enter__(self):
        agent_status(self.agent, "running", self.detail)
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, et, ev, tb):
        try:  # SPEED: every agent stage is timed
            from core import perf
            perf.record(f"scan:{self.agent.lower()} · {self.detail.split(' on ')[0][:34]}", time.perf_counter() - self._t0)
        except Exception:
            pass
        if et:
            agent_status(self.agent, "error", f"{et.__name__}: {ev}")
            log(self.agent, f"failed: {et.__name__}: {ev}", "error", tb=traceback.format_exc()[-800:])
            return True  # never let one agent kill the scan
        agent_status(self.agent, "ok", self.result or self.detail)
        return False
