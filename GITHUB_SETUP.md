# Οδηγός: Ανέβασμα στο GitHub & Τρέξιμο του Kraken Bot

Αυτός ο οδηγός καλύπτει δύο πράγματα:
1. Πώς να ανεβάσεις τον κώδικα στο GitHub (version control / backup).
2. Πώς να το **τρέξεις** — είτε τοπικά για δοκιμές, είτε 24/7 σε server (συνιστάται για live trading, αφού η στρατηγική κάνει polling κάθε λεπτό και χρειάζεται να είναι συνέχεια ενεργό).

> Δεν έχω πρόσβαση στον δικό σου λογαριασμό GitHub — αυτά τα βήματα τα κάνεις εσύ. Σου δίνω τις ακριβείς εντολές.

---

## Βήμα 0: Προαπαιτούμενα στον υπολογιστή σου

```bash
git --version      # αν δεν υπάρχει: https://git-scm.com/downloads
python3 --version  # χρειάζεσαι 3.10+
```

Χρειάζεσαι επίσης λογαριασμό GitHub (δωρεάν): https://github.com/join

---

## Βήμα 1: Δημιουργία repository στο GitHub

1. Πήγαινε στο https://github.com/new
2. Όνομα: π.χ. `kraken-hybrid-bot`
3. **Visibility: επίλεξε Private** — όχι Public. Ο κώδικας δεν περιέχει secrets (αυτά πάνε στο `.env` που δεν committάρεται ποτέ), αλλά η στρατηγική/λογική σου δεν χρειάζεται να είναι δημόσια.
4. **Μην** προσθέσεις README/.gitignore/license από το GitHub UI (τα έχουμε ήδη τοπικά) — άφησέ το άδειο repo.
5. Πάτα "Create repository".

---

## Βήμα 2: Αρχικοποίηση git τοπικά και πρώτο push

Αποσυμπίεσε το `kraken_hybrid_bot.zip` που σου έδωσα, και μέσα στον φάκελο:

```bash
cd kraken_hybrid_bot

git init
git add .
git status                 # ΕΛΕΓΞΕ: δεν πρέπει να εμφανίζεται κανένα .env εδώ!
git commit -m "Initial commit: Institutional Hybrid Engine"

git branch -M main
git remote add origin https://github.com/<το-username-σου>/kraken-hybrid-bot.git
git push -u origin main
```

Το `.gitignore` που έχει ήδη το project αποκλείει αυτόματα: `.env`, `venv/`,
`__pycache__/`, τα αρχεία log και το `state/bot_state.json`. Το
`git status` πριν το commit είναι ο πιο σημαντικός έλεγχος ασφάλειας —
**αν δεις `.env` στη λίστα, ΣΤΑΜΑΤΑ και μην κάνεις commit.**

---

## Βήμα 3: Clone εκεί που θα τρέξει το bot

### Επιλογή Α — Τοπικά στον υπολογιστή σου (μόνο για δοκιμές/DRY_RUN)
Ο υπολογιστής σου πρέπει να είναι ανοιχτός συνέχεια για να δουλέψει το
1-λεπτο polling. Καλό για αρχικό testing, **όχι ιδανικό για live trading**
24/7 (sleep mode, restarts, διακοπή ρεύματος/internet σταματάει το bot).

```bash
git clone https://github.com/<username>/kraken-hybrid-bot.git
cd kraken-hybrid-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env   # βάλε τα KRAKEN_API_KEY / KRAKEN_API_SECRET / NEWSAPI_KEY, άφησε DRY_RUN=true
python main.py
```

### Επιλογή Β — Σε φθηνό VPS (συνιστάται για 24/7 λειτουργία)
Π.χ. ένα μικρό instance σε DigitalOcean, Hetzner, AWS Lightsail κ.λπ.
(~5-10$/μήνα). Μόλις κάνεις SSH στο VPS, τα βήματα είναι ίδια με την
Επιλογή Α:

```bash
ssh user@your-server-ip
git clone https://github.com/<username>/kraken-hybrid-bot.git
cd kraken-hybrid-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py    # δοκιμαστικά πρώτα, βλέπε ότι τρέχει χωρίς σφάλματα
```

Αν τρέχει σωστά με `Ctrl+C` σταμάτησέ το και πέρνα στο Βήμα 4 για να
παραμένει ενεργό μόνιμα (όχι μόνο όσο είσαι συνδεδεμένος μέσω SSH).

---

## Βήμα 4: Να παραμένει ενεργό 24/7 (μόνο για VPS/server)

### Γρήγορη λύση: tmux
```bash
tmux new -s krakenbot
source venv/bin/activate
python main.py
# Ctrl+B μετά D για detach — συνεχίζει να τρέχει στο background
# Για να ξαναμπείς: tmux attach -t krakenbot
```
Καλό για δοκιμές, αλλά δεν ξεκινάει αυτόματα μετά από reboot του server.

### Σωστή λύση: systemd service (το project περιλαμβάνει template)
```bash
nano deploy/kraken-bot.service
# άλλαξε YOUR_LINUX_USERNAME και τα paths στο δικό σου username

sudo cp deploy/kraken-bot.service /etc/systemd/system/kraken-bot.service
sudo systemctl daemon-reload
sudo systemctl enable kraken-bot
sudo systemctl start kraken-bot

# έλεγχος:
sudo systemctl status kraken-bot
sudo journalctl -u kraken-bot -f      # ζωντανά logs
```

Αυτό κάνει αυτόματο restart αν το bot crashάρει, και ξεκινάει αυτόματα σε
κάθε reboot του server.

---

## Workflow για μελλοντικές αλλαγές

Όταν αλλάζεις κάτι (π.χ. παραμέτρους στο `config.yaml`):

**Στον υπολογιστή σου (όπου κάνεις τις αλλαγές):**
```bash
git add .
git commit -m "Tune leverage caps"
git push
```

**Στο VPS (όπου τρέχει το bot):**
```bash
cd kraken-hybrid-bot
git pull
sudo systemctl restart kraken-bot   # αν τρέχει ως systemd service
```

---

## Λίστα ελέγχου ασφάλειας πριν προχωρήσεις

- [ ] Repository στο GitHub είναι **Private**
- [ ] `git status` δεν δείχνει ποτέ `.env`
- [ ] Το Kraken API key **δεν** έχει δικαίωμα "Withdraw Funds"
- [ ] `DRY_RUN=true` για τουλάχιστον αρκετές μέρες παρακολούθησης των logs πριν το αλλάξεις
- [ ] `risk.max_leverage` και `risk.max_position_pct_of_equity` στο `config.yaml` ρυθμισμένα στη δική σου ανοχή ρίσκου — όχι στα defaults χωρίς να τα έχεις σκεφτεί
- [ ] Έχεις δοκιμάσει το `STOP` kill-switch file ότι σταματάει πράγματι το bot

Πες μου αν κάπου κολλήσεις σε κάποιο βήμα (π.χ. SSH σε VPS, ή επιλογή
πάροχου) και θα σε καθοδηγήσω πιο αναλυτικά.
