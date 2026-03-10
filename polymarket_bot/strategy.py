"""Betting strategy module - analyzes markets and selects bets.

Combines multiple signals:
1. Gamma API market metadata (volume, liquidity, time to expiry)
2. CLOB orderbook analysis (spread, depth, imbalance)
3. Web research (news sentiment, keyword patterns)
4. Statistical filters (Kelly criterion, diversification)
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .client import PolymarketClient
from .config import Config
from .gamma import GammaClient, enrich_market
from .research import MarketResearcher

logger = logging.getLogger(__name__)

HISTORY_FILE = Path(__file__).parent / "bet_history.json"


@dataclass
class BetOpportunity:
    """A potential bet identified by the strategy."""

    condition_id: str
    token_id: str
    question: str
    side_label: str  # "YES" or "NO"
    current_price: float
    estimated_fair_price: float
    edge: float
    confidence: float
    suggested_amount_usdc: float
    # New fields for transparency
    signals: dict  # breakdown of what influenced the decision
    category: str
    days_until_end: int | None
    volume: float
    spread: float


class BettingStrategy:
    """Multi-signal strategy combining market data, orderbook, and research.

    Signal weights:
    - Orderbook imbalance:     25% (buy pressure vs sell pressure)
    - Web research sentiment:  30% (news and context)
    - Liquidity/spread score:  20% (tighter spread = more reliable price)
    - Volume momentum:         15% (24h volume vs total volume ratio)
    - Time decay:              10% (markets near expiry behave differently)
    """

    SIGNAL_WEIGHTS = {
        "orderbook_imbalance": 0.25,
        "research_sentiment": 0.30,
        "liquidity_score": 0.20,
        "volume_momentum": 0.15,
        "time_decay": 0.10,
    }

    def __init__(self, client: PolymarketClient, config: Config):
        self.client = client
        self.config = config
        self.gamma = GammaClient()
        self.researcher = MarketResearcher()
        self.spent_usdc = 0.0
        self.bets_placed: list[BetOpportunity] = []
        self._load_history()

    @property
    def remaining_budget(self) -> float:
        return self.config.budget_usdc - self.spent_usdc

    def scan_markets(self) -> list[BetOpportunity]:
        """Scan markets using Gamma API and analyze each for edge."""
        logger.info("Scanning markets via Gamma API...")
        opportunities = []

        # Fetch rich market data from Gamma (sorted by volume)
        raw_markets = self.gamma.get_active_markets(limit=100)
        logger.info("Fetched %d markets from Gamma API", len(raw_markets))

        for raw in raw_markets:
            market = enrich_market(raw)

            if not self._is_eligible(market):
                continue

            opps = self._analyze_market(market)
            opportunities.extend(opps)

        # Sort by expected value (edge * confidence)
        opportunities.sort(key=lambda o: o.edge * o.confidence, reverse=True)

        logger.info(
            "Found %d opportunities (top edge: %.1f%%)",
            len(opportunities),
            opportunities[0].edge * 100 if opportunities else 0,
        )
        return opportunities

    def _is_eligible(self, market: dict) -> bool:
        """Filter out markets that aren't worth analyzing."""
        if market.get("closed") or not market.get("active", False):
            return False

        # Need both YES and NO tokens
        if not market.get("yes_token") or not market.get("no_token"):
            return False

        # Skip very low volume (< $1k) - too illiquid
        if market["volume"] < 1000:
            return False

        # Skip markets ending very soon (< 1 day) - too risky/volatile
        days = market.get("days_until_end")
        if days is not None and days < 1:
            return False

        # Skip if we already bet on this market
        already_bet_ids = {b.condition_id for b in self.bets_placed}
        if market["condition_id"] in already_bet_ids:
            return False

        return True

    def _analyze_market(self, market: dict) -> list[BetOpportunity]:
        """Full analysis of a market combining all signals."""
        opportunities = []
        question = market["question"]
        condition_id = market["condition_id"]

        # Analyze both YES and NO sides
        for side in ["yes", "no"]:
            token = market[f"{side}_token"]
            if not token:
                continue

            token_id = token.get("token_id", "")
            current_price = market[f"{side}_price"]

            # Skip extreme prices
            if current_price < 0.05 or current_price > 0.95:
                continue

            # ---- Gather all signals ----
            signals = {}

            # Signal 1: Orderbook analysis
            ob = self.client.analyze_orderbook(token_id) if not self.config.dry_run else {}
            signals["orderbook_imbalance"] = ob.get("imbalance", 0.0)
            spread = ob.get("spread", 0.10)
            signals["spread"] = spread

            # Signal 2: Web research
            research = self.researcher.research_market(
                question, market.get("description", "")
            )
            # For NO tokens, invert the sentiment
            raw_sentiment = research["sentiment"]
            signals["research_sentiment"] = raw_sentiment if side == "yes" else -raw_sentiment
            signals["has_news"] = research["has_recent_news"]
            signals["keywords"] = research["keywords_found"]

            # Signal 3: Liquidity score (tighter spread = better)
            signals["liquidity_score"] = max(0, 1.0 - spread * 10)  # 0.01 spread = 0.9

            # Signal 4: Volume momentum (24h volume / total volume)
            vol_24h = market.get("volume_24h", 0)
            vol_total = market["volume"]
            signals["volume_momentum"] = min(1.0, vol_24h / max(vol_total, 1) * 30)

            # Signal 5: Time decay
            days = market.get("days_until_end")
            if days is not None:
                if days < 3:
                    signals["time_decay"] = -0.3  # Very close to expiry, risky
                elif days < 14:
                    signals["time_decay"] = 0.1  # Medium term, slight positive
                else:
                    signals["time_decay"] = 0.0  # Far out, neutral
            else:
                signals["time_decay"] = 0.0

            # ---- Combine signals into fair price estimate ----
            composite_signal = sum(
                self.SIGNAL_WEIGHTS[key] * signals.get(key, 0.0)
                for key in self.SIGNAL_WEIGHTS
            )

            # The composite signal is a small adjustment (-0.15 to +0.15 range)
            # Apply it as a shift to the current price
            fair_price = current_price + composite_signal * 0.3
            fair_price = max(0.02, min(0.98, fair_price))

            edge = fair_price - current_price

            # Only bet when we have meaningful positive edge
            if edge < 0.03:
                continue

            # ---- Calculate confidence ----
            confidence = self._calculate_confidence(market, signals, edge)

            if confidence < self.config.min_confidence:
                continue

            # ---- Size the bet ----
            amount = self._calculate_bet_size(edge, confidence, current_price)

            if amount < self.config.min_bet_per_market:
                continue

            opportunities.append(
                BetOpportunity(
                    condition_id=condition_id,
                    token_id=token_id,
                    question=question,
                    side_label=side.upper(),
                    current_price=current_price,
                    estimated_fair_price=round(fair_price, 4),
                    edge=round(edge, 4),
                    confidence=round(confidence, 3),
                    suggested_amount_usdc=amount,
                    signals=signals,
                    category=market.get("category", ""),
                    days_until_end=market.get("days_until_end"),
                    volume=market["volume"],
                    spread=spread,
                )
            )

        return opportunities

    def _calculate_confidence(
        self, market: dict, signals: dict, edge: float
    ) -> float:
        """Estimate confidence in our edge estimate."""
        conf = 0.5  # Start neutral

        # More signals agreeing = higher confidence
        positive_signals = sum(
            1 for k in ["orderbook_imbalance", "research_sentiment", "volume_momentum"]
            if signals.get(k, 0) > 0
        )
        conf += positive_signals * 0.08

        # Has recent news = more confidence in research signal
        if signals.get("has_news"):
            conf += 0.10

        # Good liquidity = price is more reliable (but our edge is smaller)
        if signals.get("liquidity_score", 0) > 0.7:
            conf += 0.05

        # Suspiciously large edge = less confident (market knows something)
        if edge > 0.15:
            conf -= 0.15
        elif edge > 0.10:
            conf -= 0.08

        # High volume markets are harder to beat
        if market["volume"] > 500_000:
            conf -= 0.05

        # Wide spread = uncertain market, our estimate is less reliable
        if signals.get("spread", 0) > 0.05:
            conf -= 0.08

        return max(0.0, min(1.0, conf))

    def _calculate_bet_size(
        self, edge: float, confidence: float, price: float
    ) -> float:
        """Kelly criterion with proper odds calculation.

        Full Kelly: f* = (bp - q) / b
        where b = net odds (payout / stake - 1), p = prob of winning, q = 1-p

        We use quarter-Kelly for safety.
        """
        if edge <= 0 or confidence <= 0:
            return 0.0

        # Our estimated probability of winning
        p = price + edge  # fair price = our estimated probability
        q = 1.0 - p

        # Net odds received: if price is 0.40, payout is 1.00, so b = (1/0.40) - 1 = 1.5
        if price <= 0:
            return 0.0
        b = (1.0 / price) - 1.0

        # Kelly fraction
        kelly = (b * p - q) / b if b > 0 else 0
        kelly = max(0, kelly)

        # Quarter Kelly (very conservative), scaled by confidence
        fraction = kelly * 0.25 * confidence
        amount = self.remaining_budget * fraction

        # Clamp
        amount = max(self.config.min_bet_per_market, amount)
        amount = min(self.config.max_bet_per_market, amount)
        amount = min(self.remaining_budget, amount)

        return round(amount, 2)

    def execute_bets(self, opportunities: list[BetOpportunity]) -> list[dict]:
        """Execute the top betting opportunities with diversification."""
        results = []
        positions = self._get_current_position_count()
        categories_this_cycle = set()

        for opp in opportunities:
            if self.remaining_budget < self.config.min_bet_per_market:
                logger.info("Budget exhausted.")
                break

            if positions >= self.config.max_active_positions:
                logger.info("Max positions reached.")
                break

            # Diversification: max 2 bets per category per cycle
            if opp.category and categories_this_cycle.count(opp.category) >= 2:
                logger.debug("Skipping %s (category limit)", opp.question[:40])
                continue

            logger.info(
                "BET $%.2f | %s %s @ %.2f -> %.2f (edge=%.1f%%, conf=%.0f%%)",
                opp.suggested_amount_usdc,
                opp.side_label,
                opp.question[:50],
                opp.current_price,
                opp.estimated_fair_price,
                opp.edge * 100,
                opp.confidence * 100,
            )
            logger.info("  Signals: %s", {k: round(v, 3) if isinstance(v, float) else v
                                           for k, v in opp.signals.items()})

            try:
                result = self.client.place_market_buy(
                    token_id=opp.token_id,
                    amount_usdc=opp.suggested_amount_usdc,
                )
                self.spent_usdc += opp.suggested_amount_usdc
                self.bets_placed.append(opp)
                positions += 1
                if opp.category:
                    categories_this_cycle.add(opp.category)
                results.append({"opportunity": opp, "result": result, "error": None})
                self._save_history(opp)
            except Exception as e:
                logger.error("Failed to place bet: %s", e)
                results.append({"opportunity": opp, "result": None, "error": str(e)})

        return results

    def _get_current_position_count(self) -> int:
        try:
            positions = self.client.get_positions()
            return len(positions) if isinstance(positions, list) else 0
        except Exception:
            return 0

    # ---- History persistence ----

    def _load_history(self):
        """Load bet history from JSON file."""
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text())
                self.spent_usdc = data.get("total_spent", 0.0)
                logger.info(
                    "Loaded history: $%.2f spent across %d bets",
                    self.spent_usdc,
                    len(data.get("bets", [])),
                )
            except (json.JSONDecodeError, KeyError):
                logger.warning("Failed to load history, starting fresh")

    def _save_history(self, bet: BetOpportunity):
        """Append a bet to the history file."""
        data = {"total_spent": 0.0, "bets": []}
        if HISTORY_FILE.exists():
            try:
                data = json.loads(HISTORY_FILE.read_text())
            except (json.JSONDecodeError, KeyError):
                pass

        data["total_spent"] = self.spent_usdc
        # Convert signals to serializable form
        bet_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "question": bet.question,
            "side": bet.side_label,
            "token_id": bet.token_id,
            "price": bet.current_price,
            "fair_price": bet.estimated_fair_price,
            "edge": bet.edge,
            "confidence": bet.confidence,
            "amount_usdc": bet.suggested_amount_usdc,
            "category": bet.category,
            "signals": {
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in bet.signals.items()
            },
        }
        data["bets"].append(bet_record)

        HISTORY_FILE.write_text(json.dumps(data, indent=2))
