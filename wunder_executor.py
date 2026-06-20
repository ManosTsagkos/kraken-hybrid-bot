"""
wunder_executor.py
------------------
Αντικαθιστά το order_executor.py - στέλνει σήματα στο WunderTrading Signal Bot
"""

import requests
import os
from datetime import datetime
import json

class WunderExecutor:
    def __init__(self, webhook_url: str, logger, dry_run: bool = True):
        self.webhook_url = webhook_url
        self.logger = logger
        self.dry_run = dry_run
        
    def execute_decision(self, decision, state):
        """
        Στέλνει το σήμα στο WunderTrading αντί για απευθείας εκτέλεση στο Kraken
        """
        
        # Χαρτογράφηση actions → WunderTrading codes
        action_map = {
            "OPEN_LONG": "Enter-Long",
            "OPEN_SHORT": "Enter-Short",
            "STRATEGY_FLIP": "Enter-Long" if decision.direction == "long" else "Enter-Short",
            "CLOSE_POSITION": "Exit-All",
            "STAND_ASIDE": None
        }
        
        code = action_map.get(decision.action.value)
        if code is None:
            self.logger.info(f"[{datetime.now()}] No action to send (STAND_ASIDE)")
            return state
        
        # Δημιουργία payload για WunderTrading
        payload = {
            "code": code,
            "orderType": "market",
            "amountPerTradeType": "quote",  # ή "base" ανάλογα με το ζευγάρι
            "amountPerTrade": 100,          # ΠΟΣΟ ΣΕ USDT! ΠΡΟΣΑΡΜΟΣΕ!
            "leverage": decision.leverage,
            "stopLoss": {
                "priceDeviation": 0.02      # 2% stop-loss
            }
        }
        
        # Προαιρετικά: take profits
        # payload["takeProfits"] = [
        #     {"priceDeviation": 0.01, "portfolio": 0.50},
        #     {"priceDeviation": 0.02, "portfolio": 0.50}
        # ]
        
        self.logger.info(f"[{datetime.now()}] Sending signal to WunderTrading: {code}")
        self.logger.info(f"[{datetime.now()}] Payload: {json.dumps(payload, indent=2)}")
        
        if self.dry_run:
            self.logger.warning(f"[DRY RUN] Would have sent: {code}")
            return state
        
        # Αποστολή στο WunderTrading
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            
            if response.status_code == 200:
                self.logger.info(f"[{datetime.now()}] ✅ Signal sent successfully!")
                self.logger.info(f"Response: {response.text}")
                
                # Ενημέρωση state (το WunderTrading κρατάει τη θέση, αλλά κρατάμε και εμείς για την απόφαση)
                if code == "Enter-Long":
                    state.position_side = "long"
                elif code == "Enter-Short":
                    state.position_side = "short"
                elif code == "Exit-All":
                    state.position_side = None
                    
            else:
                self.logger.error(f"[{datetime.now()}] ❌ WunderTrading error: {response.status_code}")
                self.logger.error(f"Response: {response.text}")
                
        except Exception as e:
            self.logger.error(f"[{datetime.now()}] ❌ Failed to send signal: {e}")
        
        return state
    
    def get_equity_usd(self):
        """
        Το WunderTrading κρατάει το equity - εδώ μπορείς να κάνεις API call 
        στο WunderTrading για να το πάρεις, ή να το αφήσεις να υπολογιστεί μόνο του.
        """
        # Για τώρα, επιστρέφουμε ένα default value
        # Ιδανικά, θα έκανες GET request στο WunderTrading API για το τρέχον equity
        return 1000.0  # placeholder
