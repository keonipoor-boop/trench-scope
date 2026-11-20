from signals.base import Signal


class Top10(Signal):
    key, label = "top10", "TOP-10"

    def evaluate(self, coin):
        v, mx = coin.get("top10_pct"), self.conf.get("max_pct", 35)
        if v is None:
            return self.missing()
        return self.result(v <= mx, 1 - v / (mx * 2), v, f"top 10 hold {v:.1f}% (max {mx}%)")
