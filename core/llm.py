"""Optional Claude integration (why-it-ran bullets, thesis prose). No key -> returns None
and callers fall back to templates."""
from __future__ import annotations

from core.config import env, get_config
from core.events import log

_client = None


def available() -> bool:
    return bool(env("ANTHROPIC_API_KEY"))


def _get_client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    return _client


def ask(prompt: str, system: str | None = None, max_tokens: int | None = None) -> str | None:
    if not available():
        return None
    conf = get_config().get("llm", {})
    try:
        import anthropic
        kw = dict(
            model=conf.get("model", "claude-opus-5-5"),
            max_tokens=max_tokens or conf.get("max_tokens", 2000),
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": conf.get("effort", "low")},
            # On a safety decline, the API re-runs the request on a fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if system:
            kw["system"] = system
        resp = _get_client().beta.messages.create(**kw)
        if resp.stop_reason == "refusal":
            log("THESIS", "Claude declined the request; using template", "warn")
            return None
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        return text or None
    except anthropic.RateLimitError:
        log("THESIS", "Claude rate-limited; using template", "warn")
    except anthropic.APIStatusError as e:
        log("THESIS", f"Claude API error {e.status_code}; using template", "warn")
    except anthropic.APIConnectionError:
        log("THESIS", "Claude unreachable; using template", "warn")
    except Exception as e:
        log("THESIS", f"Claude call failed: {e}", "warn")
    return None


WHY_SYSTEM = ("You are a terse memecoin analyst. Given data about a pump.fun coin that ran, explain why it ran "
              "in exactly 3 bullets, each under 18 words. No hype, no financial advice. Output only the 3 lines, "
              "each starting with '- '.")


def why_bullets(coin: dict) -> list[str] | None:
    keys = ["name", "symbol", "description", "twitter", "website", "ath_mcap", "time_to_100k_min", "holders",
            "top10_pct", "unique_traders", "fees_sol", "fee_volume_pct", "kols", "x_mentions", "x_big_accounts",
            "x_top_tweets", "tweet_author", "livestream", "themes", "catalysts", "is_real",
            "tweet_text", "x_bio", "x_pinned_text", "x_community_desc", "site_text"]
    data = {k: coin.get(k) for k in keys if coin.get(k) not in (None, [], "")}
    if data.get("site_text"):
        data["site_text"] = data["site_text"][:1500]
    out = ask(f"Coin data (JSON):\n{data}", system=WHY_SYSTEM, max_tokens=600)
    if not out:
        return None
    lines = [l.lstrip("-•* ").strip() for l in out.splitlines() if l.strip()]
    return lines[:3] or None
