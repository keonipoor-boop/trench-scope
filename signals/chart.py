from signals.base import Signal


class Chart(Signal):
    """Staircase chart: mostly green, equal-size steps on even volume, no pullbacks, tiny wicks,
    usually followed by a cliff dump. Typical of bot-driven fake charts."""
    key, label = "chart", "CHART"

    def evaluate(self, coin):
        s = coin.get("staircase_score")
        if s is None:
            return self.missing("chart not sampled yet")
        mx, combo = self.conf.get("max_staircase", 0.65), self.conf.get("cliff_staircase", 0.5)
        cliff = bool(coin.get("cliff_dump"))
        fake = s >= mx or (cliff and s >= combo)
        bits = [f"staircase {s:.2f}", f"{coin.get('green_ratio', 0) * 100:.0f}% green",
                f"max pullback {coin.get('max_pullback_pct', 0):.0f}%"]
        if cliff:
            bits.append("cliff dump after peak")
        return self.result(not fake, 1 - s, s, ", ".join(bits) + (" -> stair-stepped bot chart" if fake else ""))
