import os
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")
KALSHI_PRIVATE_KEY_PATH = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./private_key.pem")
KALSHI_ENVIRONMENT = os.getenv("KALSHI_ENVIRONMENT", "demo")

DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"
LIVE_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
BASE_URL = DEMO_BASE_URL if KALSHI_ENVIRONMENT == "demo" else LIVE_BASE_URL

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
MAX_POSITION_SIZE = int(os.getenv("MAX_POSITION_SIZE", "10"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.6"))
MAX_TRADE_USD = float(os.getenv("MAX_TRADE_USD", "50"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"   # log orders, don't place them

# Kalshi-native signal tuning
IMBALANCE_WEIGHT = float(os.getenv("IMBALANCE_WEIGHT", "0.05"))  # max ~5c fair-value shift
MIN_EDGE_CENTS = float(os.getenv("MIN_EDGE_CENTS", "2"))          # required edge after fees
MIN_BOOK_DEPTH = int(os.getenv("MIN_BOOK_DEPTH", "50"))           # min total resting contracts
MAX_SPREAD_CENTS = int(os.getenv("MAX_SPREAD_CENTS", "4"))        # skip wide books

# Scalp exit / polling
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "10"))              # tick interval
TAKE_PROFIT_CENTS = int(os.getenv("TAKE_PROFIT_CENTS", "5"))     # spread we aim to capture; must clear fees
STOP_LOSS_CENTS = int(os.getenv("STOP_LOSS_CENTS", "4"))         # cross out if mark-entry <= -this
EXIT_BEFORE_CLOSE_MIN = float(os.getenv("EXIT_BEFORE_CLOSE_MIN", "2"))  # force flat before close

# Maker quoting
MAKER_FEE_FREE = os.getenv("MAKER_FEE_FREE", "false").lower() == "true"  # set True only if verified
IMPROVE_CENTS = int(os.getenv("IMPROVE_CENTS", "0"))            # 0 = join best bid; 1 = improve by 1c
MIN_IMBALANCE = float(os.getenv("MIN_IMBALANCE", "0.4"))        # directional reason to quote
REQUOTE_DRIFT_CENTS = int(os.getenv("REQUOTE_DRIFT_CENTS", "1"))  # re-quote if best bid drifts past this
MAX_QUOTE_SECONDS = int(os.getenv("MAX_QUOTE_SECONDS", "45"))   # cancel unfilled entry after this

# Binance public API — UNUSED (strategy is now Kalshi-native). Kept for btc_data.py only.
BINANCE_BASE_URL = "https://api.binance.us/api/v3"
BTC_SYMBOL = "BTCUSDT"

# Kalshi BTC series tickers to search (in priority order)
BTC_SERIES_TICKERS = ["KXBTC15M"]

# Minimum minutes remaining on a market to enter a trade
MIN_MINUTES_TO_CLOSE = 3
MAX_MINUTES_TO_CLOSE = 13
