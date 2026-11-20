"""`python run.py doctor`: test every source + endpoint, list missing keys."""
from __future__ import annotations

from core import llm
from core.config import env, get_config
from sources.base import load_sources

G, R, Y, D, X = "\033[92m", "\033[91m", "\033[93m", "\033[2m", "\033[0m"


def run_doctor() -> int:
    cfg = get_config()
    print(f"\n  TRENCH SCOPE DOCTOR  (mode: {cfg['mode']}, db: {cfg.db_path})\n")
    print("  KEYS")
    for k, why in (("HELIUS_API_KEY", "holders, fees/traders, KOL wallets"), ("X_BEARER_TOKEN", "X mentions (optional)"),
                   ("ANTHROPIC_API_KEY", "Claude bullets + thesis prose (optional)")):
        ok = bool(env(k))
        print(f"   {G + '✓' if ok else Y + '·'}{X} {k:<20} {D}{'set' if ok else 'missing'} - {why}{X}")
    print()
    sources = load_sources(cfg, only_enabled=False)
    bad = 0
    for s in sources:
        if s.is_demo != cfg.demo:
            continue
        state = "enabled" if s.enabled() else ("disabled: missing " + ", ".join(s.missing_env()) if s.missing_env() else "disabled in config")
        print(f"  {s.name.upper():<18} {D}[{s.stage}, agent {s.agent}] {state}{X}")
        try:
            checks = s.doctor()
        except Exception as e:
            checks = [("doctor", False, str(e))]
        for name, ok, detail in checks:
            bad += 0 if ok else 1
            print(f"   {G + '✓' if ok else R + '✗'}{X} {name:<55} {D}{detail}{X}")
    print(f"\n  CLAUDE  {G + 'available' if llm.available() else Y + 'not configured -> template prose'}{X}")
    if llm.available():
        out = llm.ask("Reply with the single word: ok", max_tokens=200)
        print(f"   {G + '✓' if out else R + '✗'}{X} messages.create {D}{(out or 'failed')[:40]}{X}")
    print(f"\n  {G + 'all checks passed' if not bad else R + f'{bad} check(s) failed'}{X} "
          f"{D}(the app degrades gracefully: failing sources are skipped){X}\n")
    return 0 if not bad else 1
