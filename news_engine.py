"""
news_engine.py
---------------
Catalyst & News Engine (1-minute polling).

Two responsibilities described in the strategy doc:
  A. IPO / liquidity-drain detection (OpenAI, SpaceX, Anthropic, etc.)
       - "Capital Drain" keywords  -> Risk-Off  -> cut position size
       - "AI Bubble Expansion" keywords -> Risk-On -> raise risk score on AI-related assets
  B. Geopolitical shock scoring via a Keyword Matrix (0-10).
       score > geopolitical_score_flip_threshold -> "Strategy Flip"
       (close current side, open the opposite side, without waiting for the 4H candle close)

NOTE: this keyword approach is a blunt instrument by design (the strategy doc
calls it a "Keyword Matrix") - it will produce false positives/negatives.
Treat the score as a trigger for de-risking and human review, not as proof
that a geopolitical event is actually happening.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests


class NewsProvider:
    """Interface - implement fetch_recent_headlines() against your news source."""

    def fetch_recent_headlines(self, minutes: int = 5) -> list[str]:
        raise NotImplementedError


class NewsAPIProvider(NewsProvider):
    """
    Default implementation using https://newsapi.org/ (requires a free or paid API key).
    Check NewsAPI's current rate limits and licensing terms before relying on this
    for live trading - free tiers are usually not suitable for production use.
    """

    def __init__(self, api_key: str, query: str = "crypto OR bitcoin OR fed OR geopolitics"):
        self.api_key = api_key
        self.query = query
        self._session = requests.Session()

    def fetch_recent_headlines(self, minutes: int = 5) -> list[str]:
        if not self.api_key:
            return []
        try:
            resp = self._session.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": self.query,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": 50,
                    "apiKey": self.api_key,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            articles = data.get("articles", [])
            return [
                f"{a.get('title', '')} {a.get('description', '')}".strip()
                for a in articles
            ]
        except requests.RequestException:
            return []


@dataclass
class NewsState:
    geopolitical_score: int
    capital_drain_flagged: bool
    ai_bubble_expansion_flagged: bool
    strategy_flip: bool
    matched_headlines: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


class NewsEngine:
    def __init__(self, provider: NewsProvider, ipo_keywords: dict, geopolitical_keywords: list[str],
                 geopolitical_score_flip_threshold: int = 7, poll_seconds: int = 60):
        self.provider = provider
        self.capital_drain_kw = [k.lower() for k in ipo_keywords.get("capital_drain", [])]
        self.ai_bubble_kw = [k.lower() for k in ipo_keywords.get("ai_bubble_expansion", [])]
        self.geo_kw = [k.lower() for k in geopolitical_keywords]
        self.flip_threshold = geopolitical_score_flip_threshold
        self.poll_seconds = poll_seconds

    def _score_geopolitical(self, headlines_lower: list[str]) -> tuple[int, list[str]]:
        """
        Very simple keyword-matrix scorer: each distinct matched geopolitical
        keyword across all headlines adds 2 points (capped at 10), and headlines
        matching MORE THAN ONE keyword count extra to reflect compounding severity.
        This is intentionally simple/transparent - tune freely.
        """
        matched_keywords = set()
        matched_headlines = []
        for h in headlines_lower:
            hits = [kw for kw in self.geo_kw if kw in h]
            if hits:
                matched_keywords.update(hits)
                matched_headlines.append(h)
        score = min(10, len(matched_keywords) * 2 + max(0, len(matched_headlines) - len(matched_keywords)))
        return score, matched_headlines

    def poll(self) -> NewsState:
        headlines = self.provider.fetch_recent_headlines()
        headlines_lower = [h.lower() for h in headlines]

        geo_score, geo_matches = self._score_geopolitical(headlines_lower)
        capital_drain = any(kw in h for h in headlines_lower for kw in self.capital_drain_kw)
        ai_bubble = any(kw in h for h in headlines_lower for kw in self.ai_bubble_kw)
        strategy_flip = geo_score > self.flip_threshold

        reasons = []
        if not headlines:
            reasons.append("No headlines retrieved this cycle (check NEWSAPI_KEY / connectivity)")
        if geo_score:
            reasons.append(f"Geopolitical keyword score = {geo_score}/10 (flip threshold {self.flip_threshold})")
        if capital_drain:
            reasons.append("IPO capital-drain keywords matched -> reduce position size")
        if ai_bubble:
            reasons.append("AI bubble-expansion keywords matched -> raise risk score on AI-correlated assets")
        if strategy_flip:
            reasons.append("Geopolitical score exceeds flip threshold -> STRATEGY FLIP triggered")
        if not reasons:
            reasons.append("No catalyst signals this cycle")

        return NewsState(
            geopolitical_score=geo_score,
            capital_drain_flagged=capital_drain,
            ai_bubble_expansion_flagged=ai_bubble,
            strategy_flip=strategy_flip,
            matched_headlines=geo_matches[:10],
            reasons=reasons,
        )
