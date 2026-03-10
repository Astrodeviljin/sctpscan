#!/bin/bash
# Setup script for Polymarket Betting Bot
set -e

echo "=== Polymarket Betting Bot Setup ==="

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -r polymarket_bot/requirements.txt

# Create .env if it doesn't exist
if [ ! -f "polymarket_bot/.env" ]; then
    echo "Creating .env from template..."
    cp polymarket_bot/.env.example polymarket_bot/.env
    echo ""
    echo "*** IMPORTANT: Edit polymarket_bot/.env with your Polymarket private key ***"
    echo "To export your key: Polymarket Settings > Private Key > Start Export"
fi

echo ""
echo "Setup complete! Usage:"
echo ""
echo "  # Activate venv first"
echo "  source venv/bin/activate"
echo ""
echo "  # Dry run (simulation, no real money)"
echo "  cd polymarket_bot && python -m polymarket_bot --dry-run --once"
echo ""
echo "  # Live trading (REAL MONEY - be careful!)"
echo "  cd polymarket_bot && python -m polymarket_bot --once"
echo ""
echo "  # Continuous mode (runs every 5 minutes)"
echo "  cd polymarket_bot && python -m polymarket_bot"
echo ""
