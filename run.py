#!/usr/bin/env python3
"""Trench Scope CLI.

  python run.py scan                 one scan (discover -> enrich -> signals -> catalysts)
  python run.py backfill --days 7    scan with a custom lookback
  python run.py thesis daily|hourly  write a thesis now
  python run.py serve                dashboard + API only (http://localhost:8000)
  python run.py daemon               scheduler + dashboard
  python run.py demo                 seed fake data, then daemon in demo mode (no keys needed)
  python run.py doctor               test every source/endpoint, show missing keys
  python run.py                      same as `start`: scheduler + dashboard (what hosts run)
Add --demo to any command to run it against the demo database.
"""
import argparse
import os
import sys

from core.config import load_config


def main():
    p = argparse.ArgumentParser(description="Trench Scope")
    p.add_argument("cmd", nargs="?", default="start",
                   choices=["start", "scan", "backfill", "thesis", "serve", "daemon", "demo", "doctor"])
    p.add_argument("kind", nargs="?", choices=["daily", "hourly"])
    p.add_argument("--days", type=float, default=7)
    # cloud hosts (Render/Railway/Fly) pass PORT and need 0.0.0.0
    p.add_argument("--host", default="0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", 8000)))
    p.add_argument("--demo", action="store_true", help="use demo sources + demo db")
    p.add_argument("--no-api", action="store_true", help="daemon without the web server")
    a = p.parse_args()

    demo = a.demo or a.cmd == "demo"
    cfg = load_config(mode="demo" if demo else None)

    from core import pipeline, thesis
    from core.db import get_db
    get_db()

    if a.cmd == "doctor":
        from core.doctor import run_doctor
        sys.exit(run_doctor())
    if a.cmd == "scan":
        print(pipeline.scan())
    elif a.cmd == "backfill":
        print(pipeline.scan(backfill_days=a.days))
    elif a.cmd == "thesis":
        t = thesis.generate(a.kind or "daily")
        print("\n" + t["text"] + "\n")
        for b in t["data"]["look_for"]:
            print("  LOOK FOR  " + b)
        for b in t["data"]["avoid"]:
            print("  AVOID     " + b)
    elif a.cmd == "serve":
        serve(a.host, a.port)
    elif a.cmd in ("daemon", "demo", "start"):
        if a.cmd == "demo":
            db = get_db()
            if not (db.kv_get("last_scan") or {}).get("ts"):
                print("seeding demo trenches (7 days)...")
            pipeline.scan()
            thesis.generate("daily")
            thesis.generate("hourly")
        from core.scheduler import Scheduler
        s = Scheduler()
        if a.no_api:
            s.loop()
        else:
            s.start_background()
            serve(a.host, a.port)


def serve(host, port):
    import uvicorn
    from api.server import app
    print(f"\n  TRENCH SCOPE  ->  http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
