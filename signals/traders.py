from signals.base import Signal


class Traders(Signal):
    key, label = "traders", "TRADERS"

    def evaluate(self, coin):
        v, mn = coin.get("unique_traders"), self.conf.get("min", 150)
        if v is None:
            return self.missing()
        return self.result(v >= mn, v / (mn * 3), v, f"~{v:,} unique traders (min {mn})")
