"""Tiny scheduler: scan every N min, hourly thesis at :MM, daily thesis at HH:00 local."""
from __future__ import annotations

import threading
import time
from datetime import datetime

from core import pipeline, thesis
from core.config import get_config
from core.db import get_db
from core.events import log


class Scheduler:
    def __init__(self):
        self.stop = threading.Event()
        self.lock = threading.Lock()  # one job at a time

    def _run(self, name, fn):
        with self.lock:
            try:
                fn()
            except Exception as e:
                log("SCOUT", f"job {name} crashed: {e}", "error")

    def loop(self):
        cfg = get_config()
        sc = cfg.get("scheduler", {})
        db = get_db()
        every = sc.get("scan_every_min", 10) * 60
        log("SCOUT", f"scheduler up: scan every {every // 60}m, hourly at :{sc.get('hourly_at_minute', 0):02d}, "
                     f"daily at {sc.get('daily_at_hour', 8):02d}:00 local")
        last_scan = (db.kv_get("last_scan") or {}).get("ts", 0)
        from core import kol_research  # KOL LAB profiles refresh hourly on their own worker
        kol_research.start_worker()
        from core import sniper
        sniper.start_worker()  # SNIPER DESK: paper-trading sniper, its own loop
        if not cfg.demo and cfg.get_path("sources.pumpportal.enabled", True):
            from sources import pumpportal  # live launch/migration feed runs between scans
            pumpportal.start(cfg.get_path("sources.pumpportal.url", "wss://pumpportal.fun/api/data"))
        while not self.stop.is_set():
            now = time.time()
            dt = datetime.now()
            if now - last_scan >= every:
                last_scan = now
                self._run("scan", pipeline.scan)
                # fresh install / restart: don't make people wait until 8am for a thesis
                for kind, max_age in (("daily", 86400), ("hourly", 3600)):
                    t = thesis.latest(kind)
                    if not t or now - t["ts"] > max_age:
                        self._run(kind, lambda k=kind: thesis.generate(k))
                        d = datetime.now()
                        if kind == "hourly":
                            db.kv_set("hourly_done", d.strftime("%Y%m%d%H"))
                        elif d.hour >= sc.get("daily_at_hour", 8):
                            db.kv_set("daily_done", d.strftime("%Y%m%d"))
            hour_key = dt.strftime("%Y%m%d%H")
            if dt.minute >= sc.get("hourly_at_minute", 0) and db.kv_get("hourly_done") != hour_key:
                db.kv_set("hourly_done", hour_key)
                self._run("hourly", lambda: thesis.generate("hourly"))
            day_key = dt.strftime("%Y%m%d")
            if dt.hour >= sc.get("daily_at_hour", 8) and db.kv_get("daily_done") != day_key:
                db.kv_set("daily_done", day_key)
                self._run("daily", lambda: thesis.generate("daily"))
            self.stop.wait(5)

    def start_background(self):
        t = threading.Thread(target=self.loop, daemon=True, name="scheduler")
        t.start()
        return t
