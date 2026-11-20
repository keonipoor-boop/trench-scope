from catalysts.base import CatalystRule


class KolCall(CatalystRule):
    category = "kol_call"

    def check(self, coin, ctx):
        kols = coin.get("kols") or []
        if not kols:
            return None
        window = ctx["cfg"].get_path("catalysts.kol_call_window_min", 60) * 60
        created = coin.get("created_ts") or 0
        early = [k for k in kols if k.get("ts") and k["ts"] - created <= window]
        if early:
            names = ", ".join(k["name"] for k in early[:3])
            return self.category, 65 + 5 * min(len(early), 4), f"Early KOL buys within {window // 60}m: {names}"
        return self.category, 30, f"{len(kols)} KOL(s) bought later: {', '.join(k['name'] for k in kols[:3])}"
