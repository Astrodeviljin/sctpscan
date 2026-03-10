"""Polymarket CLOB API client wrapper."""

import logging
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    MarketOrderArgs,
    OrderArgs,
    OrderType,
    ApiCreds,
)
from py_clob_client.order_builder.constants import BUY, SELL

from .config import Config

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Wrapper around py-clob-client for simplified betting operations."""

    def __init__(self, config: Config):
        self.config = config
        self.client = None
        self.api_creds = None

    def connect(self):
        """Initialize and authenticate the CLOB client."""
        logger.info("Connecting to Polymarket CLOB API...")

        self.client = ClobClient(
            host=self.config.host,
            chain_id=self.config.chain_id,
            key=self.config.private_key,
            signature_type=self.config.signature_type,
            funder=self.config.funder_address or None,
        )

        # Verify connection
        ok = self.client.get_ok()
        if ok != "OK":
            raise ConnectionError(f"CLOB API health check failed: {ok}")

        # Derive API credentials (L2 auth)
        self.api_creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self.api_creds)

        logger.info("Connected and authenticated successfully.")

    def get_balance(self) -> float:
        """Get USDC balance in human-readable format."""
        balance_wei = self.client.get_balance()
        return float(balance_wei) / 1e6

    def get_markets(self) -> list:
        """Fetch available markets."""
        return self.client.get_markets()

    def get_market(self, condition_id: str) -> dict:
        """Fetch a specific market by condition ID."""
        return self.client.get_market(condition_id)

    def get_orderbook(self, token_id: str) -> dict:
        """Get order book for a token."""
        return self.client.get_order_book(token_id)

    def get_price(self, token_id: str, side: str = "buy") -> float:
        """Get current price for a token."""
        price = self.client.get_price(token_id, side)
        return float(price)

    def get_midpoint(self, token_id: str) -> float:
        """Get midpoint price for a token."""
        mid = self.client.get_midpoint(token_id)
        return float(mid)

    def get_positions(self) -> list:
        """Get current open positions."""
        return self.client.get_positions()

    def place_market_buy(self, token_id: str, amount_usdc: float) -> dict:
        """Place a market buy order (fill-or-kill).

        Args:
            token_id: The token ID to buy (YES or NO outcome).
            amount_usdc: Amount in USDC to spend.

        Returns:
            Order response from the API.
        """
        if self.config.dry_run:
            logger.info(
                "[DRY RUN] Would buy token %s for $%.2f", token_id, amount_usdc
            )
            return {"dry_run": True, "token_id": token_id, "amount": amount_usdc}

        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount_usdc,
            side=BUY,
            order_type=OrderType.FOK,
        )
        signed_order = self.client.create_market_order(order_args)
        resp = self.client.post_order(signed_order)
        logger.info("Market buy order placed: %s", resp)
        return resp

    def place_limit_buy(
        self, token_id: str, price: float, size: float, tick_size: str = "0.01"
    ) -> dict:
        """Place a limit buy order.

        Args:
            token_id: The token ID to buy.
            price: Price per share (0.01 to 0.99).
            size: Number of shares to buy.
            tick_size: Minimum price increment.

        Returns:
            Order response from the API.
        """
        if self.config.dry_run:
            logger.info(
                "[DRY RUN] Would limit buy token %s at $%.2f x %.1f shares",
                token_id,
                price,
                size,
            )
            return {
                "dry_run": True,
                "token_id": token_id,
                "price": price,
                "size": size,
            }

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY,
        )
        signed_order = self.client.create_order(order_args)
        resp = self.client.post_order(signed_order, OrderType.GTC)
        logger.info("Limit buy order placed: %s", resp)
        return resp

    def cancel_all_orders(self) -> dict:
        """Cancel all open orders."""
        if self.config.dry_run:
            logger.info("[DRY RUN] Would cancel all orders")
            return {"dry_run": True}
        return self.client.cancel_all()
