from signals.base import Signal


class Kols(Signal):
    key, label = "kols", "KOLS"

    def evaluate(self, coin):
        k = coin.get("kols")
        if k is None:
            return self.missing("KOL tracker not run")
        mn = self.conf.get("min", 1)
        names = ", ".join(x["name"] for x in k[:4])
        return self.result(len(k) >= mn, len(k) / 3, len(k), f"{len(k)} KOL buyer(s){': ' + names if names else ''}")
