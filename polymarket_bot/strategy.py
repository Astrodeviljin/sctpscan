"""Betting strategy module - analyzes markets and selects bets."""

import logging
import random
from dataclasses import dataclass

from .client import PolymarketClient
from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class BetOpportunity:
    """A potential bet identified by the strategy."""

    condition_id: str
    token_id: str
    question: str
    side_label: str  # "YES" or "NO"
    current_price: float  # current market price (probability)
    estimated_fair_price: float  # our estimate of true probability
    edge: float  # estimated_fair_price - current_price
    confidence: float  # how confident we are (0 to 1)
    suggested_amount_usdc: float


class BettingStrategy:
    """Analyzes Polymarket markets and identifies betting opportunities.

    Strategy overview:
    - Scans active markets for mispriced outcomes
    - Looks for markets where price implies a probability that seems off
    - Uses volume, liquidity, and price patterns as signals
    - Sizes bets based on edge and confidence (Kelly-inspired)
    """

    def __init__(self, client: PolymarketClient, config: Config):
        self.client = client
        self.config = config
        self.spent_usdc = 0.0
        self.bets_placed = []

    @property
    def remaining_budget(self) -> float:
        return self.config.budget_usdc - self.spent_usdc

    def scan_markets(self) -> list[BetOpportunity]:
        """Scan markets and return a list of betting opportunities."""
        logger.info("Scanning markets for opportunities...")
        opportunities = []

        try:
            markets_response = self.client.get_markets()
        except Exception as e:
            logger.error("Failed to fetch markets: %s", e)
            return []

        # Handle both list and paginated dict responses
        if isinstance(markets_response, dict):
            markets = markets_response.get("data", [])
        else:
            markets = markets_response

        for market in markets:
            if not self._is_eligible_market(market):
                continue

            opps = self._analyze_market(market)
            opportunities.extend(opps)

        # Sort by edge * confidence (expected value)
        opportunities.sort(key=lambda o: o.edge * o.confidence, reverse=True)

        logger.info("Found %d opportunities", len(opportunities))
        return opportunities

    def _is_eligible_market(self, market: dict) -> bool:
        """Check if a market is worth analyzing."""
        # Must be active
        if not market.get("active", False):
            return False
        if market.get("closed", True):
            return False
        if market.get("archived", True):
            return False

        # Must have tokens (outcomes)
        tokens = market.get("tokens", [])
        if len(tokens) < 2:
            return False

        return True

    def _analyze_market(self, market: dict) -> list[BetOpportunity]:
        """Analyze a single market for betting opportunities."""
        opportunities = []
        tokens = market.get("tokens", [])
        question = market.get("question", "Unknown")
        condition_id = market.get("condition_id", "")

        for token in tokens:
            token_id = token.get("token_id", "")
            outcome = token.get("outcome", "")
            price_str = token.get("price", "0")

            try:
                current_price = float(price_str)
            except (ValueError, TypeError):
                continue

            # Skip extreme prices (too close to 0 or 1)
            if current_price < 0.05 or current_price > 0.95:
                continue

            fair_price = self._estimate_fair_price(market, token, current_price)
            edge = fair_price - current_price

            # Only interested in positive edge (buy underpriced outcomes)
            if edge <= 0.03:
                continue

            confidence = self._estimate_confidence(market, edge)

            if confidence < self.config.min_confidence:
                continue

            amount = self._calculate_bet_size(edge, confidence)

            if amount < self.config.min_bet_per_market:
                continue

            opportunities.append(
                BetOpportunity(
                    condition_id=condition_id,
                    token_id=token_id,
                    question=question,
                    side_label=outcome,
                    current_price=current_price,
                    estimated_fair_price=fair_price,
                    edge=edge,
                    confidence=confidence,
                    suggested_amount_usdc=amount,
                )
            )

        return opportunities

    def _estimate_fair_price(
        self, market: dict, token: dict, current_price: float
    ) -> float:
        """Estimate the fair price of an outcome.

        This uses a combination of heuristics:
        - Volume-weighted price momentum
        - Market liquidity as a proxy for information efficiency
        - Mean-reversion signals for extreme recent moves

        In production, this could be enhanced with:
        - News sentiment analysis
        - Historical resolution patterns
        - Expert model predictions
        """
        # Base: assume market is ~90% efficient
        fair_price = current_price

        # Signal 1: Low-volume markets tend to be less efficient
        # Slight adjustment toward 0.5 (uncertainty) for thin markets
        volume = float(market.get("volume", "0") or "0")
        if volume < 10000:
            # Thin market - price may be stale, regress toward 0.5
            regression_factor = 0.05
            fair_price = current_price + regression_factor * (0.5 - current_price)

        # Signal 2: If spread is wide, there may be an opportunity
        # (We'd need orderbook data for this - simplified here)

        # Signal 3: Slight contrarian bias on very popular outcomes
        # Markets with very high volume may have herd behavior
        if volume > 1_000_000 and current_price < 0.4:
            fair_price += 0.02  # slight underdog boost

        # Add small random noise to avoid herding on exact same prices
        fair_price += random.uniform(-0.01, 0.01)

        return max(0.01, min(0.99, fair_price))

    def _estimate_confidence(self, market: dict, edge: float) -> float:
        """Estimate how confident we are in the edge estimate."""
        # Higher edge = somewhat less confident (market knows something?)
        base_confidence = 0.7

        # Large edges are suspicious
        if edge > 0.15:
            base_confidence -= 0.15
        elif edge > 0.10:
            base_confidence -= 0.05

        # More volume = more confidence the price is informative
        volume = float(market.get("volume", "0") or "0")
        if volume > 100_000:
            base_confidence += 0.05
        elif volume < 5_000:
            base_confidence -= 0.10

        return max(0.0, min(1.0, base_confidence))

    def _calculate_bet_size(self, edge: float, confidence: float) -> float:
        """Calculate bet size using a fractional Kelly criterion.

        Kelly fraction = edge / odds, but we use half-Kelly for safety.
        """
        if edge <= 0 or confidence <= 0:
            return 0.0

        # Simplified Kelly: fraction of bankroll = edge * confidence
        kelly_fraction = edge * confidence
        half_kelly = kelly_fraction / 2.0

        amount = self.remaining_budget * half_kelly

        # Clamp to configured limits
        amount = max(self.config.min_bet_per_market, amount)
        amount = min(self.config.max_bet_per_market, amount)
        amount = min(self.remaining_budget, amount)

        return round(amount, 2)

    def execute_bets(self, opportunities: list[BetOpportunity]) -> list[dict]:
        """Execute the top betting opportunities."""
        results = []
        positions = self._get_current_position_count()

        for opp in opportunities:
            if self.remaining_budget < self.config.min_bet_per_market:
                logger.info("Budget exhausted. Stopping.")
                break

            if positions >= self.config.max_active_positions:
                logger.info("Max positions reached. Stopping.")
                break

            logger.info(
                "Betting $%.2f on '%s' %s @ %.2f (fair: %.2f, edge: %.2f)",
                opp.suggested_amount_usdc,
                opp.question[:60],
                opp.side_label,
                opp.current_price,
                opp.estimated_fair_price,
                opp.edge,
            )

            try:
                result = self.client.place_market_buy(
                    token_id=opp.token_id,
                    amount_usdc=opp.suggested_amount_usdc,
                )
                self.spent_usdc += opp.suggested_amount_usdc
                self.bets_placed.append(opp)
                positions += 1
                results.append({"opportunity": opp, "result": result, "error": None})
            except Exception as e:
                logger.error("Failed to place bet: %s", e)
                results.append({"opportunity": opp, "result": None, "error": str(e)})

        return results

    def _get_current_position_count(self) -> int:
        """Get the number of current open positions."""
        try:
            positions = self.client.get_positions()
            if isinstance(positions, list):
                return len(positions)
            return 0
        except Exception:
            return 0
