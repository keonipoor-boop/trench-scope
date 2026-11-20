from signals.base import Signal


class Holders(Signal):
    key, label = "holders", "HOLDERS"

    def evaluate(self, coin):
        v, mn = coin.get("holders"), self.conf.get("min", 300)
        if v is None:
            return self.missing()
        cap = " (capped sample)" if coin.get("holders_capped") else ""
        return self.result(v >= mn, v / (mn * 2), v, f"{v:,} holders vs min {mn}{cap}")
