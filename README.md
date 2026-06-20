# Institutional Hybrid Engine — Kraken Trading Bot

Υλοποίηση της στρατηγικής "Institutional Hybrid Engine": 4ωρο τεχνικό
υπόστρωμα (EMA 9/21/55 + RSI 14 + ROC 10) σε συνδυασμό με 1-λεπτο macro/news
engine (VIX, DXY, IPO events, γεωπολιτικά σοκ) που μπορεί να ανατρέψει τη
θέση άμεσα.

## ⚠️ Πριν κάνεις οτιδήποτε άλλο

- **Δεν έχω δοκιμάσει αυτόν τον κώδικα έναντι του ζωντανού Kraken API.** Το
  sandbox μέσα στο οποίο τον έγραψα δεν έχει πρόσβαση δικτύου στο
  `api.kraken.com`. Έχω επιβεβαιώσει το authentication scheme byte-for-byte
  έναντι του επίσημου παραδείγματος του Kraken, και έχω τεστάρει όλη τη
  λογική (δείκτες, decision matrix) με συνθετικά δεδομένα — αλλά **εσύ
  πρέπει να το τρέξεις πρώτα σε `DRY_RUN=true` για αρκετές μέρες/εβδομάδες
  πριν βάλεις πραγματικό κεφάλαιο.**
- `DRY_RUN=true` (default) σημαίνει ότι κάθε εντολή στέλνεται στο Kraken με
  `validate=true` — το Kraken ελέγχει την εντολή για σφάλματα αλλά **ποτέ
  δεν εκτελείται πραγματική συναλλαγή**. Αυτό είναι μηχανισμός του ίδιου
  του Kraken, όχι κάτι που προσομοιώνω εγώ.
- Margin/leverage trading στο Kraken έχει γεωγραφικούς περιορισμούς (π.χ.
  διαφορετικοί κανόνες για US/UK/Canada) — επιβεβαίωσε ότι ο λογαριασμός
  σου είναι eligible πριν θέσεις `leverage` > 1.
- Δεν είμαι χρηματοοικονομικός σύμβουλος. Αυτό είναι ένα εργαλείο λογισμικού
  που εκτελεί τους κανόνες που μου έδωσες — δεν αξιολογώ αν η ίδια η
  στρατηγική είναι κερδοφόρα.

## Δομή

```
kraken_hybrid_bot/
├── main.py              # ο βρόχος orchestration
├── kraken_client.py      # Kraken REST API (auth, OHLC, AddOrder, ...)
├── indicators.py         # EMA / RSI / ROC + 4ωρο trend
├── macro_engine.py        # VIX / DXY risk-off detection (yfinance, pluggable)
├── news_engine.py          # IPO + geopolitical keyword scoring (NewsAPI, pluggable)
├── decision_engine.py       # η Μήτρα Αποφάσεων από το strategy doc
├── risk_manager.py          # ΣΚΛΗΡΑ όρια ασφαλείας (leverage cap, daily circuit breaker)
├── order_executor.py        # μετατρέπει αποφάσεις σε πραγματικές Kraken εντολές
├── state.py                 # τοπικό JSON state (θέση, entry, stop)
├── logger_setup.py
├── config.yaml             # ΟΛΕΣ οι παράμετροι στρατηγικής
├── .env.example            # API keys + DRY_RUN switch
└── tests/test_indicators.py
```

## Εγκατάσταση

```bash
cd kraken_hybrid_bot
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# άνοιξε το .env και βάλε τα δικά σου KRAKEN_API_KEY / KRAKEN_API_SECRET
```

Δημιουργία API key: https://www.kraken.com/u/security/api
Δικαιώματα που χρειάζονται: **Query Funds, Query Open/Closed Orders, Create
& Modify Orders, Cancel/Close Orders, Query Open Positions**.
**ΜΗΝ** ενεργοποιήσεις "Withdraw Funds" σε αυτό το key.

## Tρέξιμο των tests (χωρίς δίκτυο, συνθετικά δεδομένα)

```bash
python -m pytest tests/ -v
# ή: python tests/test_indicators.py
```

## Τρέξιμο του bot

```bash
python main.py
```

Θα δεις στο log: `Running in DRY_RUN mode...` — αυτό είναι το default και
σκόπιμο. Άφησέ το έτσι μέχρι να έχεις παρακολουθήσει τις αποφάσεις του bot
(στο `logs/bot.log`) για αρκετό καιρό και να συμφωνείς με κάθε ένα.

### Πέρασμα σε live trading

Άλλαξε στο `.env`:
```
DRY_RUN=false
```
Συνιστάται ανεπιφύλακτα: ξεκίνα με πολύ μικρό κεφάλαιο, χαμηλό
`max_position_pct_of_equity` (π.χ. 5%) και `max_leverage: 1` στο
`config.yaml`, και ανέβασε σταδιακά μόνο αφού έχεις δει πραγματική
συμπεριφορά για αρκετό διάστημα.

### Emergency stop

Δημιούργησε ένα αρχείο με όνομα `STOP` στον φάκελο του project — το bot το
ελέγχει κάθε κύκλο και σταματά αμέσως. (Δεν κλείνει αυτόματα ανοιχτές θέσεις
— αυτό γίνεται χειροκίνητα από το Kraken UI ή τηλεφωνικά μέσω της δικής σου
εντολής.)

## Σημαντικά κενά / σημεία προς επέκταση

Αυτά είναι σκόπιμα απλοποιημένα — δες τα σχόλια στον κώδικα:

1. **VIX/DXY μέσω `yfinance`**: δωρεάν, ανεπίσημο, χωρίς SLA. Για κεφάλαιο
   που μετράει, αντικατέστησέ το με paid provider (Alpha Vantage, Twelve
   Data, Polygon.io) υλοποιώντας το `MacroDataProvider` interface στο
   `macro_engine.py`.
2. **News scoring μέσω NewsAPI.org**: το δωρεάν tier έχει περιορισμούς
   ρυθμού/καθυστέρησης — δες τους τρέχοντες όρους τους πριν βασιστείς σε
   αυτό για live trading.
3. **`INCREASE_CONVICTION`** (αύξηση leverage σε ανοιχτή θέση) απλώς κάνει
   log προς το παρόν — δεν αλλάζει πραγματικά το leverage μιας ανοιχτής
   θέσης, γιατί αυτό σημαίνει close+reopen στο Kraken Spot Margin και θέλει
   προσεκτική απόφαση από εσένα για το αν αξίζει το κόστος/ρίσκο.
4. **Trailing stop**: καταγράφεται λογικά (`TIGHTEN_STOP`) αλλά δεν
   ενημερώνει αυτόματα μια υπάρχουσα conditional order στο Kraken μέσω
   `EditOrder` — θα το χρειαστείς αν θες πραγματικό exchange-side
   enforcement αντί για τοπικό tracking.
5. **`_close_position` / `_reduce_position`** διαβάζουν τον πραγματικό όγκο
   θέσης από το `OpenPositions` του Kraken πριν κλείσουν (καλό) αλλά δεν
   χειρίζονται partial fills/πολλαπλές ανοιχτές θέσεις στο ίδιο pair με
   ιδιαίτερη λεπτομέρεια.

## Risk management που έχω προσθέσει (όχι ζητημένο ρητά, αλλά σημαντικό)

- **Daily circuit breaker** (`risk.max_daily_loss_pct`, default 5%): αν το
  equity πέσει πάνω από αυτό το ποσοστό μέσα στην ημέρα, το bot σταματά να
  ανοίγει ΝΕΕΣ θέσεις (τις υπάρχουσες μπορεί ακόμα να τις κλείσει/προστατέψει).
- **Hard leverage/position-size caps** (`risk.max_leverage`,
  `risk.max_position_pct_of_equity`): ποτέ δεν ξεπερνιούνται, ό,τι και να
  ζητήσει η λογική της στρατηγικής.

Αυτά τα δύο όρια είναι τα πιο σημαντικά να ρυθμίσεις στο `config.yaml`
σύμφωνα με τη δική σου ανοχή ρίσκου πριν βάλεις πραγματικά χρήματα.
