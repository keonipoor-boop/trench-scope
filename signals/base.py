"""Base class for signals. Each signal reads its thresholds from config.json -> signals.<key>
and returns {passed, score, value, note}. passed=None means "no data yet"."""
from __future__ import annotations


class Signal:
    key: str = "base"        # config key under "signals"
    label: str = "BASE"      # short label shown on signal bars
    abstract = False

    def __init__(self, conf: dict, cfg=None):
        self.conf, self.cfg = conf, cfg

    @property
    def required(self) -> bool:
        return bool(self.conf.get("required", True))

    def evaluate(self, coin: dict) -> dict:
        raise NotImplementedError

    @staticmethod
    def result(passed, score, value, note):
        return {"passed": passed, "score": round(max(0.0, min(1.0, score)), 3) if score is not None else 0.0,
                "value": value, "note": note}

    @staticmethod
    def missing(note="no data yet"):
        return {"passed": None, "score": 0.0, "value": None, "note": note}


def load_signals(cfg) -> list[Signal]:
    from core.registry import discover
    out = []
    for cls in discover("signals", Signal):
        conf = cfg.get("signals", {}).get(cls.key, {})
        if conf.get("enabled", True):
            out.append(cls(conf, cfg))
    return out


def evaluate_all(coin: dict, signals: list[Signal]) -> tuple[dict, bool | None]:
    """is_real: False if any required check FAILED, None if none failed but some lack data
    (still being analysed), True only when every required check passed."""
    res, real, pending = {}, True, False
    for s in signals:
        try:
            r = s.evaluate(coin)
        except Exception as e:
            r = Signal.missing(f"error: {e}")
        r["required"] = s.required
        r["label"] = s.label
        res[s.key] = r
        if r["passed"] is None and s.conf.get("allow_missing"):
            continue  # e.g. chart not sampled yet: don't block REAL, decide when data arrives
        if s.required and r["passed"] is False:
            real = False
        elif s.required and r["passed"] is None:
            pending = True
    return res, (False if not real else None if pending else True)
