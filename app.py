from flask import Flask

app = Flask(__name__)

# --- Εδώ μπαίνει ο κώδικας του trading bot σου ---
def run_trading_bot():
    # ...
    # Η λογική του bot σου, π.χ. έλεγχος αγοράς/πώλησης
    # ...
    return "Bot executed successfully" # Επιστρέφεις ένα μήνυμα επιτυχίας

# --- Αυτό είναι το endpoint που θα καλεί το cron-job.org ---
@app.route('/')
def home():
    # Κάθε φορά που κάποιος (π.χ. το cron-job.org) επισκέπτεται το "/",
    # καλείται αυτή η συνάρτηση, η οποία με τη σειρά της τρέχει το bot σου.
    result = run_trading_bot()
    return f"Trading bot ran at: {result}"

# --- Αυτό είναι απαραίτητο για να τρέξει ο server ---
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)