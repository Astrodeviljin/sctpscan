#!/usr/bin/env python3
"""Polymarket Betting Bot - Main orchestrator.

Connects to Polymarket, scans markets, identifies opportunities,
and places bets autonomously within configured budget limits.

Usage:
    python -m polymarket_bot.bot [--once] [--dry-run]
"""

import argparse
import logging
import signal
import sys
import time

from .config import Config
from .client import PolymarketClient
from .strategy import BettingStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

RUNNING = True


def signal_handler(sig, frame):
    global RUNNING
    logger.info("Shutdown signal received. Stopping after current cycle...")
    RUNNING = False


def print_summary(strategy: BettingStrategy, balance: float):
    """Print a summary of the bot's state."""
    print("\n" + "=" * 60)
    print("  POLYMARKET BETTING BOT - STATUS")
    print("=" * 60)
    print(f"  USDC Balance:        ${balance:.2f}")
    print(f"  Budget Total:        ${strategy.config.budget_usdc:.2f}")
    print(f"  Budget Spent:        ${strategy.spent_usdc:.2f}")
    print(f"  Budget Remaining:    ${strategy.remaining_budget:.2f}")
    print(f"  Bets Placed:         {len(strategy.bets_placed)}")
    print(f"  Dry Run:             {strategy.config.dry_run}")
    print("=" * 60)

    if strategy.bets_placed:
        print("\n  Recent Bets:")
        for bet in strategy.bets_placed[-10:]:
            print(
                f"    - ${bet.suggested_amount_usdc:.2f} on "
                f"'{bet.question[:50]}' {bet.side_label} "
                f"@ {bet.current_price:.2f}"
            )
    print()


def run_bot(config: Config, run_once: bool = False):
    """Main bot loop."""
    client = PolymarketClient(config)

    logger.info("Initializing Polymarket Betting Bot...")
    if config.dry_run:
        logger.info("*** DRY RUN MODE - No real orders will be placed ***")

    # Connect to Polymarket
    client.connect()
    balance = client.get_balance()
    logger.info("USDC Balance: $%.2f", balance)

    if not config.dry_run and balance < config.min_bet_per_market:
        logger.error(
            "Insufficient balance ($%.2f). Need at least $%.2f",
            balance,
            config.min_bet_per_market,
        )
        sys.exit(1)

    strategy = BettingStrategy(client, config)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    cycle = 0
    while RUNNING:
        cycle += 1
        logger.info("--- Cycle %d ---", cycle)

        try:
            # Scan for opportunities
            opportunities = strategy.scan_markets()

            if opportunities:
                logger.info(
                    "Top opportunity: '%s' %s @ %.2f (edge: %.2f)",
                    opportunities[0].question[:50],
                    opportunities[0].side_label,
                    opportunities[0].current_price,
                    opportunities[0].edge,
                )

                # Execute bets
                results = strategy.execute_bets(opportunities)

                successes = sum(1 for r in results if r["error"] is None)
                failures = sum(1 for r in results if r["error"] is not None)
                logger.info(
                    "Cycle %d complete: %d bets placed, %d failed",
                    cycle,
                    successes,
                    failures,
                )
            else:
                logger.info("No opportunities found this cycle.")

            # Print summary
            try:
                balance = client.get_balance()
            except Exception:
                pass
            print_summary(strategy, balance)

        except Exception as e:
            logger.error("Error in cycle %d: %s", cycle, e, exc_info=True)

        if run_once:
            break

        if strategy.remaining_budget < config.min_bet_per_market:
            logger.info("Budget fully allocated. Bot stopping.")
            break

        logger.info("Sleeping %d seconds until next scan...", config.poll_interval_seconds)
        for _ in range(config.poll_interval_seconds):
            if not RUNNING:
                break
            time.sleep(1)

    logger.info("Bot stopped. Total spent: $%.2f across %d bets.",
                strategy.spent_usdc, len(strategy.bets_placed))


def main():
    parser = argparse.ArgumentParser(description="Polymarket Betting Bot")
    parser.add_argument(
        "--once", action="store_true", help="Run a single scan cycle then exit"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate without placing real orders"
    )
    args = parser.parse_args()

    config = Config.from_env()

    if args.dry_run:
        config.dry_run = True

    if not config.private_key and not config.dry_run:
        logger.error(
            "POLYMARKET_PRIVATE_KEY not set. Set it in .env or use --dry-run mode."
        )
        sys.exit(1)

    run_bot(config, run_once=args.once)


if __name__ == "__main__":
    main()
