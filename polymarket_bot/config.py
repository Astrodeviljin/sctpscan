"""Configuration for the Polymarket betting bot."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # Polymarket connection
    host: str = "https://clob.polymarket.com"
    chain_id: int = 137  # Polygon mainnet
    private_key: str = ""
    signature_type: int = 1  # 0=EOA, 1=Email/Magic, 2=Gnosis
    funder_address: str = ""

    # Budget management
    budget_usdc: float = 1080.0
    max_bet_per_market: float = 50.0
    min_bet_per_market: float = 5.0

    # Strategy
    min_confidence: float = 0.65
    max_active_positions: int = 20

    # Operational
    poll_interval_seconds: int = 300
    dry_run: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
            signature_type=int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "1")),
            funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),
            budget_usdc=float(os.getenv("BETTING_BUDGET_USDC", "1080.0")),
            max_bet_per_market=float(os.getenv("MAX_BET_PER_MARKET", "50.0")),
            min_bet_per_market=float(os.getenv("MIN_BET_PER_MARKET", "5.0")),
            min_confidence=float(os.getenv("MIN_CONFIDENCE", "0.65")),
            max_active_positions=int(os.getenv("MAX_ACTIVE_POSITIONS", "20")),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "300")),
            dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        )
