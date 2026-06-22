"""
Entry point for the Kalshi 15-min BTC trading bot.

Usage:
    python main.py

The bot runs a decision cycle every 60 seconds, looking for open BTC markets
that close within the next 3-13 minutes. When it finds one and the signal
is strong enough, it places a limit order.

Prerequisites:
    1. Copy .env.example → .env and fill in your credentials.
    2. Generate an RSA key pair and register the public key on Kalshi:
         openssl genrsa -out private_key.pem 2048
         openssl rsa -in private_key.pem -pubout -out public_key.pem
       Then upload public_key.pem at https://kalshi.com/profile/api-keys
    3. Install dependencies:  pip install -r requirements.txt
    4. Start in demo mode first (KALSHI_ENVIRONMENT=demo in .env).
"""

import logging
import signal
import sys
import time

import schedule

import config
from bot import TradingBot


def setup_logging():
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=[
        logging.StreamHandler(sys.stdout),
    ])


def validate_config():
    errors = []
    if not config.KALSHI_API_KEY_ID:
        errors.append("KALSHI_API_KEY_ID is not set")
    import os
    if not os.path.exists(config.KALSHI_PRIVATE_KEY_PATH):
        errors.append(f"Private key not found at {config.KALSHI_PRIVATE_KEY_PATH!r}")
    if errors:
        for e in errors:
            logging.error("Config error: %s", e)
        sys.exit(1)


def main():
    setup_logging()
    validate_config()

    logger = logging.getLogger("main")
    logger.info("Starting Kalshi BTC 15-min bot | env=%s | min_conf=%.2f | max_usd=%.2f",
                config.KALSHI_ENVIRONMENT, config.MIN_CONFIDENCE, config.MAX_TRADE_USD)

    bot = TradingBot()

    # Run immediately on start, then every 60 seconds
    bot.run_cycle()

    schedule.every(60).seconds.do(bot.run_cycle)
    schedule.every(5).minutes.do(bot.reset_active_ticker_if_expired)

    # Graceful shutdown on Ctrl-C or SIGTERM
    def _shutdown(sig, frame):
        logger.info("Shutdown signal received — exiting.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Bot running. Press Ctrl-C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(1)


if __name__ == "__main__":
    main()
