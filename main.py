"""
main.py
--------
Institutional Hybrid Engine - orchestrator.

Loop structure:
  - Every `technical.refresh_seconds` (default 15 min): re-pull 4H candles
    from Kraken and recompute the technical trend snapshot (cheap to do
    more often than the 4H candle itself closes - cost is one API call).
  - Every `macro.poll_seconds` / `news.poll_seconds` (default 60s each):
    poll VIX/DXY and headlines, re-run the decision engine, and execute
    whatever action it returns (subject to the risk manager's hard caps).

Run with:
    python main.py

Stop safely at any time by creating a file named STOP in this directory
(checked every loop iteration), or Ctrl+C (graceful shutdown, does not
auto-close open positions - close manually or via Kraken's own UI/stop orders).
"""

from __future__ import annotations

import os
import time

api_key = os.environ.get('KRAKEN_API_KEY')
api_secret = os.environ.get('KRAKEN_API_SECRET')
news_key = os.environ.get('NEWSAPI_KEY')
dry_run = os.environ.get('DRY_RUN')

import yaml
from dotenv import load_dotenv

from decision_engine import DecisionEngine
from indicators import compute_technical_snapshot, kraken_ohlc_to_dataframe
from kraken_client import KrakenAPIError, KrakenClient
from logger_setup import setup_logger
from macro_engine import MacroEngine, YFinanceMacroProvider
from news_engine import NewsAPIProvider, NewsEngine
from order_executor import OrderExecutor
from risk_manager import RiskManager
from state import load_state, save_state

from flask import Flask
import time
from datetime import datetime
import os

app = Flask(__name__)

# ------------------------------------------------------------
# 1. Ορισμός των συναρτήσεων του bot
# ------------------------------------------------------------
def run_fast_loop():
    """Γρήγορο loop: μακρο, νέα, απόφαση, εκτέλεση"""
    print(f"[{datetime.now()}] Running fast loop...")
    # Εδώ βάζεις ό,τι έκανες στο while loop σου (μακρο/νέα)
    return "No action"

def run_technical_refresh():
    """Ανανέωση τεχνικών δεδομένων (4H candles)"""
    print(f"[{datetime.now()}] Refreshing technical data...")
    # Εδώ βάζεις την κλήση στο Kraken για τα κεριά
    return "Technical data updated"

# ------------------------------------------------------------
# 2. Global μεταβλητή για το πότε έγινε το τελευταίο refresh
# ------------------------------------------------------------
last_technical_refresh = 0

# ------------------------------------------------------------
# 3. Το route που καλεί το cron-job.org
# ------------------------------------------------------------
@app.route('/')
def home():
    global last_technical_refresh

    now = time.time()
    print(f"[{datetime.now()}] Bot called by cron-job.org")

    # Πάντα τρέχουμε το γρήγορο loop
    try:
        result = run_fast_loop()
        print(f"[{datetime.now()}] Fast loop result: {result}")
    except Exception as e:
        print(f"[{datetime.now()}] ERROR in fast loop: {e}")
        return f"Error: {e}", 500

    # Κάθε 15 λεπτά (900 sec) ανανεώνουμε τα τεχνικά
    if now - last_technical_refresh >= 900:
        print(f"[{datetime.now()}] Refreshing technical data (4H candles)...")
        try:
            run_technical_refresh()
            last_technical_refresh = now
            print(f"[{datetime.now()}] Technical data refreshed")
        except Exception as e:
            print(f"[{datetime.now()}] ERROR in technical refresh: {e}")

    return "OK", 200

# ------------------------------------------------------------
# 4. (Προαιρετικό) Τοπική εκτέλεση για testing
# ------------------------------------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
  
def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def main():
    load_dotenv()
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

    state = load_state(op_cfg["state_file"])
    logger.info(f"Loaded state: {state}")

    tech_snapshot = None
    last_tech_refresh = 0.0
    poll_interval = min(macro_cfg["poll_seconds"], news_cfg["poll_seconds"])

    logger.info(f"Starting main loop. pair={pair} poll_interval={poll_interval}s "
                f"4H refresh every {tech_cfg['refresh_seconds']}s")

    while True:
        if os.path.exists(op_cfg["kill_switch_file"]):
            logger.warning(f"Kill switch file '{op_cfg['kill_switch_file']}' detected - halting loop. "
                            f"Existing positions are NOT auto-closed; manage them manually if needed.")
            break

        now = time.time()

        # --- refresh 4H technical snapshot periodically ---
        if tech_snapshot is None or (now - last_tech_refresh) >= tech_cfg["refresh_seconds"]:
            try:
                ohlc = client.get_ohlc(pair, interval=tech_cfg["candle_interval_minutes"])
                pair_key = next(k for k in ohlc.keys() if k != "last")
                df = kraken_ohlc_to_dataframe(ohlc, pair_key)
                tech_snapshot = compute_technical_snapshot(
                    df,
                    ema_fast=tech_cfg["ema_fast"], ema_mid=tech_cfg["ema_mid"], ema_slow=tech_cfg["ema_slow"],
                    rsi_period=tech_cfg["rsi_period"],
                    rsi_bull_threshold=tech_cfg["rsi_bull_threshold"],
                    rsi_bear_threshold=tech_cfg["rsi_bear_threshold"],
                    roc_period=tech_cfg["roc_period"],
                    roc_exhaustion_lookback=tech_cfg["roc_exhaustion_lookback"],
                )
                last_tech_refresh = now
                logger.info(f"4H technical snapshot refreshed: trend={tech_snapshot.trend} "
                            f"close={tech_snapshot.close} rsi={tech_snapshot.rsi:.1f}")
            except (KrakenAPIError, ValueError, StopIteration) as exc:
                logger.error(f"Failed to refresh technical snapshot: {exc}")
                time.sleep(poll_interval)
                continue

        # --- 1-minute macro + news polling ---
        try:
            macro_state = macro_engine.poll()
            news_state = news_engine.poll()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on provider hiccups
            logger.error(f"Macro/news polling failed this cycle: {exc}")
            time.sleep(poll_interval)
            continue

        decision = decision_engine.decide(tech_snapshot, macro_state, news_state, state.position_side)

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

        state = executor.execute(decision, risk_result, state)
        save_state(op_cfg["state_file"], state)

        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
