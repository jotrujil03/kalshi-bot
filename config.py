import os
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./private_key.pem")
KALSHI_ENVIRONMENT = os.getenv("KALSHI_ENVIRONMENT", "demo")

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
LIVE_BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"
BASE_URL = DEMO_BASE_URL if KALSHI_ENVIRONMENT == "demo" else LIVE_BASE_URL

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_POSITION_SIZE = int(os.getenv("MAX_POSITION_SIZE", "10"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.6"))
MAX_TRADE_USD = float(os.getenv("MAX_TRADE_USD", "50"))

# Binance public API — no auth required
BINANCE_BASE_URL = "https://api.binance.com/api/v3"
BTC_SYMBOL = "BTCUSDT"

# Kalshi BTC series tickers to search (in priority order)
BTC_SERIES_TICKERS = ["KXBTC", "BTCZ", "BTC"]

# Minimum minutes remaining on a market to enter a trade
MIN_MINUTES_TO_CLOSE = 3
MAX_MINUTES_TO_CLOSE = 13
