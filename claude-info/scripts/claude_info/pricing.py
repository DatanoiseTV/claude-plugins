"""Per-model token pricing, loaded from pricing.json next to this file.

Costs are list-price estimates. They are what the same traffic would cost on
the API, which for subscription users is a measure of usage, not a bill.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PRICING_FILE = Path(__file__).with_name("pricing.json")

# Claude Code may append a context-size tag ("claude-opus-4-6[1m]") and older
# ids carry a date snapshot ("claude-sonnet-4-5-20250929"). Neither changes
# the price, so both are stripped before the prefix lookup.
_SUFFIX_RE = re.compile(r"(\[[^\]]*\])$")
_DATE_RE = re.compile(r"-\d{8}$")


@dataclass(frozen=True)
class Usage:
    """Token counts of a single API request."""

    input: int = 0
    cw5m: int = 0
    cw1h: int = 0
    read: int = 0
    output: int = 0
    fast: bool = False
    geo_us: bool = False

    @property
    def prompt(self) -> int:
        """Prompt length as used for tiered pricing: all input categories."""
        return self.input + self.cw5m + self.cw1h + self.read


@dataclass(frozen=True)
class Rates:
    """USD per million tokens for one model, already tier/speed adjusted."""

    input: float
    cw5m: float
    cw1h: float
    read: float
    output: float

    def cost(self, u: Usage) -> float:
        return (
            u.input * self.input
            + u.cw5m * self.cw5m
            + u.cw1h * self.cw1h
            + u.read * self.read
            + u.output * self.output
        ) / 1e6


class Pricing:
    def __init__(self, data: dict):
        self._models: dict = data.get("models", {})
        self._families: dict = {
            k: v for k, v in data.get("families", {}).items() if not k.startswith("_")
        }
        mods = data.get("modifiers", {})
        self._geo_mult: float = float(mods.get("geo_us_multiplier", 1.0))
        self._geo_prefixes: tuple = tuple(mods.get("geo_us_applies_from", []))
        # Longest prefix first, so "claude-opus-4-5" wins over "claude-opus-4".
        self._prefixes = sorted(self._models, key=len, reverse=True)

    @classmethod
    def load(cls, path: Path = PRICING_FILE) -> "Pricing":
        with path.open("r", encoding="utf-8") as f:
            return cls(json.load(f))

    @staticmethod
    def normalize(model_id: str) -> str:
        m = (model_id or "").strip().lower()
        m = _SUFFIX_RE.sub("", m)
        m = _DATE_RE.sub("", m)
        # Bedrock ids carry a vendor prefix and a version suffix.
        if m.startswith("anthropic."):
            m = m[len("anthropic."):]
        m = re.sub(r"-v\d+(:\d+)?$", "", m)
        return m

    def resolve(self, model_id: str) -> Optional[str]:
        """Return the pricing key for a model id, or None if it is unknown."""
        m = self.normalize(model_id)
        if not m or m.startswith("<"):  # "<synthetic>" entries cost nothing
            return None
        for p in self._prefixes:
            if m == p or m.startswith(p + "-") or m.startswith(p + "["):
                return p
        for fam, key in self._families.items():
            if fam in m:
                return key
        return None

    def info(self, model_id: str) -> Optional[dict]:
        key = self.resolve(model_id)
        return self._models.get(key) if key else None

    def rates(self, model_id: str, usage: Optional[Usage] = None) -> Optional[Rates]:
        """Effective rates for a request, applying long-prompt tier, fast
        mode and US-only inference where they apply."""
        key = self.resolve(model_id)
        if key is None:
            return None
        base = self._models[key]
        r = {k: float(base[k]) for k in ("input", "cw5m", "cw1h", "read", "output")}
        usage = usage or Usage()

        long_tier = base.get("long")
        if long_tier and usage.prompt > int(long_tier.get("threshold", 0)):
            r.update({k: float(long_tier[k]) for k in r if k in long_tier})

        fast = base.get("fast")
        if usage.fast and fast:
            # Caching multipliers apply on top of the fast-mode input price,
            # so every input-derived category scales by the same factor.
            scale = float(fast["input"]) / r["input"]
            for k in ("input", "cw5m", "cw1h", "read"):
                r[k] *= scale
            r["output"] = float(fast["output"])

        if usage.geo_us and key.startswith(self._geo_prefixes):
            r = {k: v * self._geo_mult for k, v in r.items()}

        return Rates(**r)

    def cost(self, model_id: str, usage: Usage) -> float:
        rates = self.rates(model_id, usage)
        return rates.cost(usage) if rates else 0.0


_default: Optional[Pricing] = None


def default() -> Pricing:
    """Process-wide pricing table, loaded on first use."""
    global _default
    if _default is None:
        _default = Pricing.load()
    return _default
