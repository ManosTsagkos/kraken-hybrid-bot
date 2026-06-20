"""
main.py
--------
Institutional Hybrid Engine - orchestrator (Flask version for Render + cron-job.org)
"""

from __future__ import annotations

import os
import time
from datetime import datetime
import yaml
from dotenv import load_dotenv
from flask import Flask

# Imports από τα δικά σου modules
from decision_engine import DecisionEngine
from indicators import compute_technical_snapshot, kraken_ohlc_to_dataframe
from kraken_client import KrakenAPIError, KrakenClient
from logger_setup import setup_logger
from macro_engine import MacroEngine, YFinanceMacroProvider
from news_engine import NewsAPIProvider, NewsEngine
from order_executor import OrderExecutor
from risk_manager import RiskManager
from state import load_state, save_state

# ------------------------------------------------------------
# Δημιουργία Flask app
# ------------------------------------------------------------
app = Flask(__name__)

# ------------------------------------------------------------
# Φόρτωση config και αρχικοποίηση components (παγκόσμια)
# ------------------------------------------------------------
load_dotenv()

def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)

cfg = load_config()

logger = setup_logger(
    "hybrid_engine",
    cfg["operational"]["log_file"],
    cfg["operational"]["log_level"],
)

dry_run = os.getenv("DRY_RUN", "true").strip().lower() in ("1", "true", "yes")
if dry_run:
    logger.warning("Running in DRY_RUN mode - Kraken will validate but NEVER execute orders. "
                   "Set DRY_RUN=false in .env only after you have reviewed behaviour thoroughly.")
else:
    logger.warning("!!! LIVE TRADING MODE - DRY_RUN=false - REAL ORDERS WILL BE SENT WITH REAL FUNDS !!!")

client = KrakenClient(
    api_key=os.getenv("KRAKEN_API_KEY", ""),
    api_secret=os.getenv("KRAKEN_API_SECRET", ""),
    dry_run=dry_run,
)

pair = cfg["exchange"]["pair"]
tech_cfg = cfg["technical"]
macro_cfg = cfg["macro"]
news_cfg = cfg["news"]
risk_cfg = cfg["risk"]
op_cfg = cfg["operational"]

macro_engine = MacroEngine(
    provider=YFinanceMacroProvider(),
    vix_risk_off_threshold=macro_cfg["vix_risk_off_threshold"],
    vix_calm_threshold=macro_cfg["vix_calm_threshold"],
    dxy_spike_window_minutes=macro_cfg["dxy_spike_window_minutes"],
    dxy_spike_pct_threshold=macro_cfg["dxy_spike_pct_threshold"],
    poll_seconds=macro_cfg["poll_seconds"],
)
news_engine = NewsEngine(
    provider=NewsAPIProvider(api_key=os.getenv("NEWSAPI_KEY", "")),
    ipo_keywords=news_cfg["ipo_keywords"],
    geopolitical_keywords=news_cfg["geopolitical_keywords"],
    geopolitical_score_flip_threshold=news_cfg["geopolitical_score_flip_threshold"],
    poll_seconds=news_cfg["poll_seconds"],
)
decision_engine = DecisionEngine(risk_cfg, macro_cfg, news_cfg)
risk_manager = RiskManager(
    max_leverage=risk_cfg["max_leverage"],
    max_position_pct_of_equity=risk_cfg["max_position_pct_of_equity"],
    max_daily_loss_pct=risk_cfg["max_daily_loss_pct"],
    default_stop_loss_pct=risk_cfg["default_stop_loss_pct"],
)
executor = OrderExecutor(client, pair, logger, risk_cfg["default_stop_loss_pct"])

# Φόρτωση αρχικής κατάστασης
state = load_state(op_cfg["state_file"])
logger.info(f"Loaded initial state: {state}")

# ------------------------------------------------------------
# Global μεταβλητές για την κατάσταση του bot
# ------------------------------------------------------------
tech_snapshot = None
last_tech_refresh = 0.0

# ------------------------------------------------------------
# Αρχική ανανέωση τεχνικών δεδομένων (για να έχουμε snapshot από την αρχή)
# ------------------------------------------------------------
logger.info("Performing initial technical refresh...")
try:
    ohlc = client.get_ohlc(pair, interval=tech_cfg["candle_interval_minutes"])
    pair_key = next(k for k in ohlc.keys() if k != "last")
    df = kraken_ohlc_to_dataframe(ohlc, pair_key)
    tech_snapshot = compute_technical_snapshot(
        df,
        ema_fast=tech_cfg["ema_fast"],
        ema_mid=tech_cfg["ema_mid"],
        ema_slow=tech_cfg["ema_slow"],
        rsi_period=tech_cfg["rsi_period"],
        rsi_bull_threshold=tech_cfg["rsi_bull_threshold"],
        rsi_bear_threshold=tech_cfg["rsi_bear_threshold"],
        roc_period=tech_cfg["roc_period"],
        roc_exhaustion_lookback=tech_cfg["roc_exhaustion_lookback"],
    )
    last_tech_refresh = time.time()
    logger.info(f"Initial technical snapshot loaded: trend={tech_snapshot.trend}, close={tech_snapshot.close}, rsi={tech_snapshot.rsi:.1f}")
except Exception as e:
    logger.error(f"Initial technical refresh failed: {e}")
    tech_snapshot = None

# ------------------------------------------------------------
# Συναρτήσεις που εκτελούν τη λογική
# ------------------------------------------------------------
def run_technical_refresh():
    """Ανανέωση τεχνικών δεδομένων (4H candles) – καλείται κάθε 15 λεπτά"""
    global tech_snapshot, last_tech_refresh

    try:
        ohlc = client.get_ohlc(pair, interval=tech_cfg["candle_interval_minutes"])
        pair_key = next(k for k in ohlc.keys() if k != "last")
        df = kraken_ohlc_to_dataframe(ohlc, pair_key)
        tech_snapshot = compute_technical_snapshot(
            df,
            ema_fast=tech_cfg["ema_fast"],
            ema_mid=tech_cfg["ema_mid"],
            ema_slow=tech_cfg["ema_slow"],
            rsi_period=tech_cfg["rsi_period"],
            rsi_bull_threshold=tech_cfg["rsi_bull_threshold"],
            rsi_bear_threshold=tech_cfg["rsi_bear_threshold"],
            roc_period=tech_cfg["roc_period"],
            roc_exhaustion_lookback=tech_cfg["roc_exhaustion_lookback"],
        )
        last_tech_refresh = time.time()
        logger.info(f"Technical refresh OK: trend={tech_snapshot.trend}, close={tech_snapshot.close}, rsi={tech_snapshot.rsi:.1f}")
        return "Technical data updated"
    except (KrakenAPIError, ValueError, StopIteration, Exception) as e:
        logger.error(f"Error in technical refresh: {e}", exc_info=True)
        raise  # το πιάνει το try/except στο route

def run_fast_loop():
    """Γρήγορο loop: macro, news, decision, execution (καλείται κάθε λεπτό)"""
    global state, tech_snapshot, last_tech_refresh

    # Αν το tech_snapshot είναι None, προσπάθησε να το ανανεώσεις άμεσα
    if tech_snapshot is None:
        logger.warning("tech_snapshot is None, attempting immediate refresh...")
        try:
            run_technical_refresh()
        except Exception as e:
            logger.error(f"Immediate technical refresh failed: {e}")
            return "No technical data available - skipping decision"

    try:
        # 1. Poll μακροοικονομικών και ειδήσεων
        macro_state = macro_engine.poll()
        news_state = news_engine.poll()

        # 2. Λήψη απόφασης
        decision = decision_engine.decide(tech_snapshot, macro_state, news_state, state.position_side)

        # 3. Έλεγχος equity και risk
        equity = executor.get_equity_usd()
        is_opening_new_risk = decision.action.value in (
            "OPEN_LONG", "OPEN_SHORT", "STRATEGY_FLIP", "INCREASE_CONVICTION",
        )
        risk_result = risk_manager.evaluate(
            current_equity=equity,
            requested_leverage=decision.leverage,
            requested_pct_of_equity=risk_cfg["max_position_pct_of_equity"] * (decision.position_size_pct_of_max / 100.0),
            is_opening_new_risk=is_opening_new_risk,
        )

        # 4. Εκτέλεση εντολής
        state = executor.execute(decision, risk_result, state)
        save_state(op_cfg["state_file"], state)

        result_msg = f"Action: {decision.action.value}, Leverage: {decision.leverage}, Equity: {equity:.2f}"
        logger.info(f"Fast loop executed: {result_msg}")
        return result_msg

    except Exception as e:
        logger.error(f"Error in fast loop: {e}", exc_info=True)
        raise  # το πιάνει το try/except στο route

# ------------------------------------------------------------
# Route που καλεί το cron-job.org
# ------------------------------------------------------------
@app.route('/')
def home():
    global last_tech_refresh

    now = time.time()
    print(f"[{datetime.now()}] Bot called by cron-job.org")

    # 1. Πάντα τρέχουμε το γρήγορο loop
    try:
        result = run_fast_loop()
        print(f"[{datetime.now()}] Fast loop result: {result}")
    except Exception as e:
        print(f"[{datetime.now()}] ERROR in fast loop: {e}")
        return f"Error in fast loop: {e}", 500

    # 2. Κάθε 15 λεπτά (900 sec) ανανεώνουμε τα τεχνικά
    if now - last_tech_refresh >= 900:
        print(f"[{datetime.now()}] Refreshing technical data (4H candles)...")
        try:
            run_technical_refresh()
            print(f"[{datetime.now()}] Technical data refreshed")
        except Exception as e:
            print(f"[{datetime.now()}] ERROR in technical refresh: {e}")
            # Δεν επιστρέφουμε error για να μην χαλάσει το cron-job

    return "OK", 200

# ------------------------------------------------------------
# Εκκίνηση (για τοπικό testing)
# ------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
