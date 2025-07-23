import os
from flask import Flask, render_template_string, request, redirect
import requests
from bs4 import BeautifulSoup
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from threading import Thread
import time

app = Flask(__name__)

@app.route('/')
def home():
    return 'Hello from Render!'

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

# === CONFIGURATION ===
ALERTS_FILE = "alerts.json"
if not os.path.exists(ALERTS_FILE):
    with open(ALERTS_FILE, 'w') as f:
        json.dump([], f)

# EMAIL SETTINGS (example using Gmail or Resend)
EMAIL_PROVIDER = "resend"  # or "gmail"
RESEND_API_KEY = "re_your_api_key_here"
GMAIL_EMAIL = "you@gmail.com"
GMAIL_APP_PASSWORD = "your_password"

# STORES TO SUPPORT
STORES = {
    "coles": {
        "base": "https://www.coles.com.au ",
        "search": "/search?q={sku}",
        "price_sel": "span.price__value",
        "name_sel": "h1[data-testid='product-title']"
    },
    "woolworths": {
        "base": "https://www.woolworths.com.au ",
        "search": "/shop/search/products?searchTerm={sku}",
        "price_sel": "strong.price",
        "name_sel": "a.product-tile--title-link"
    },
    "amazon": {
        "base": "https://www.amazon.com.au ",
        "search": "/s?k={sku}",
        "price_sel": ".a-price-whole",
        "name_sel": "h2 a.a-link-normal"
    },
    "ebay": {
        "base": "https://www.ebay.com.au ",
        "search": "/sch/i.html?_nkw={sku}",
        "price_sel": ".s-item__price",
        "name_sel": ".s-item__title"
    },
    "jbhifi": {
        "base": "https://www.jbhifi.com.au ",
        "search": "/search?q={sku}",
        "price_sel": "span.price",
        "name_sel": "a.product-tile__title"
    },
    "officeworks": {
        "base": "https://www.officeworks.com.au ",
        "search": "/shop/search?q={sku}",
        "price_sel": "span.price__value",
        "name_sel": "a.product-tile__title"
    },
    "harveynorman": {
        "base": "https://www.harveynorman.com.au ",
        "search": "/search?q={sku}",
        "price_sel": "span.price",
        "name_sel": "h4.product-name a"
    }
}

# === HELPERS ===
def send_email(to_email, subject, body):
    try:
        if EMAIL_PROVIDER == "resend":
            import resend
            resend.api_key = RESEND_API_KEY
            params = {
                "from": "DealAlert <alert@dealalert.com>",
                "to": to_email,
                "subject": subject,
                "html": body
            }
            resend.Emails.send(params)
            print(f"✅ Email sent to {to_email}")
        elif EMAIL_PROVIDER == "gmail":
            msg = MIMEText(body, "html")
            msg["Subject"] = subject
            msg["From"] = GMAIL_EMAIL
            msg["To"] = to_email

            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
            server.quit()
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

def extract_sku(url):
    """Try to find SKU/ID from URL"""
    # Examples:
    # Coles: .../product/...-8462360 → 8462360
    # JB HiFi: /product/xxxxxx/sku-123456 → 123456
    patterns = [
        r'/(\d{5,})',           # Any 5+ digit number
        r'sku[-=](\d+)',        # sku=12345 or sku-12345
        r'p-(\d+)',             # p-12345
        r'itemcode=(\d+)'       # ?itemcode=12345
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    # Fallback: last digits in path
    match = re.search(r'/(\d+)[^/]*$', url)
    return match.group(1) if match else None

def get_store_price(store_key, sku):
    store = STORES[store_key]
    search_url = store["base"] + store["search"].format(sku=sku)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        price_el = soup.select_one(store["price_sel"])
        name_el = soup.select_one(store["name_sel"])

        if not price_el:
            return None, None

        price_text = price_el.get_text(strip=True)
        price = float(re.search(r'\d+(?:\.\d+)?', price_text.replace(',', '')).group())

        name = name_el.get_text(strip=True) if name_el else f"Product {sku}"

        return {
            "price": price,
            "name": name,
            "url": search_url,
            "store": store_key.capitalize()
        }, None
    except Exception as e:
        return None, str(e)

# === BACKGROUND MONITORING ===
def start_monitoring():
    while True:
        with open(ALERTS_FILE, 'r') as f:
            alerts = json.load(f)

        updated_alerts = []
        for alert in alerts:
            if alert.get('notified'):
                updated_alerts.append(alert)
                continue

            matched_stores = []
            for store in alert['stores']:
                data, err = get_store_price(store, alert['sku'])
                if data:
                    threshold = alert['retail_price'] * (1 - alert['discount_rate']/100)
                    if data['price'] <= threshold:
                        matched_stores.append(data)

            if matched_stores:
                subject = f"🎉 Deal Found Across {len(matched_stores)} Stores!"
                items_list = "".join([
                    f"<li><b>{i['store']}</b>: {i['name']} – ${i['price']} <a href='{i['url']}'>View</a></li>"
                    for i in matched_stores
                ])
                body = f"""
                <h2>🔥 Price Alert Triggered!</h2>
                <p>Your desired discount ({alert['discount_rate']}%) was found:</p>
                <ul>{items_list}</ul>
                """
                send_email(alert['email'], subject, body)
                alert['notified'] = True
                alert['trigger_time'] = time.time()

            updated_alerts.append(alert)

        with open(ALERTS_FILE, 'w') as f:
            json.dump(updated_alerts, f)

        time.sleep(1800)  # Every 30 mins

Thread(target=start_monitoring, daemon=True).start()

# === HTML FORM WITH CHECKBOXES ===
FORM_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>🛒 Multi-Store Price Tracker</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap @5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="p-4">
<div class="container">
  <h1 class="mb-3">🛒 DealFinder</h1>
  <p class="text-muted">Enter a product link. We'll extract the ID and let you track it across multiple stores.</p>

  <form method="POST" action="/submit">
    <div class="mb-3">
      <label>Product URL (e.g., Coles, Amazon, etc.)</label>
      <input type="url" name="url" class="form-control" required placeholder="https://www.coles.com.au/product/... ">
    </div>

    <div class="mb-3">
      <label>Retail Price ($) – Original price you're comparing against</label>
      <input type="number" step="0.01" name="retail_price" class="form-control" required>
    </div>

    <div class="mb-3">
      <label>Target Discount Rate (%)</label>
      <input type="number" name="discount_rate" class="form-control" min="1" max="100" required placeholder="e.g., 20 for 20% off">
    </div>

    <div class="mb-3">
      <label>Your Email</label>
      <input type="email" name="email" class="form-control" required>
    </div>

    <button type="submit" class="btn btn-primary">➡️ Next: Select Stores to Monitor</button>
  </form>
</div>
</body>
</html>
'''

SELECT_STORES_HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Select Stores</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap @5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="p-4">
<div class="container">
  <h2>🔍 Found Product ID: <code>{{sku}}</code></h2>
  <p>Select where you want to track this item:</p>

  <form method="POST" action="/confirm">
    <input type="hidden" name="url" value="{{url}}">
    <input type="hidden" name="retail_price" value="{{retail_price}}">
    <input type="hidden" name="discount_rate" value="{{discount_rate}}">
    <input type="hidden" name="email" value="{{email}}">
    <input type="hidden" name="sku" value="{{sku}}">

    {% for store in stores %}
    <div class="form-check mb-2">
      <input class="form-check-input" type="checkbox" name="stores" value="{{store}}" id="store_{{store}}" checked>
      <label class="form-check-label" for="store_{{store}}">
        {{store|capitalize}}
      </label>
    </div>
    {% endfor %}

    <button type="submit" class="btn btn-success">🔔 Set Multi-Store Alert</button>
  </form>
</div>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(FORM_HTML)

@app.route('/submit', methods=['POST'])
def submit():
    url = request.form['url']
    retail_price = float(request.form['retail_price'])
    discount_rate = float(request.form['discount_rate'])
    email = request.form['email']

    sku = extract_sku(url)
    if not sku:
        return "<h1>❌ Could not extract product ID from URL.</h1><p>Make sure it contains a number (like 12345).</p>", 400

    # Render checkbox page
    return render_template_string(
        SELECT_STORES_HTML,
        sku=sku,
        url=url,
        retail_price=retail_price,
        discount_rate=discount_rate,
        email=email,
        stores=STORES.keys()
    )

@app.route('/confirm', methods=['POST'])
def confirm():
    # Get all data
    alert = {
        "url": request.form['url'],
        "sku": request.form['sku'],
        "retail_price": float(request.form['retail_price']),
        "discount_rate": float(request.form['discount_rate']),
        "email": request.form['email'],
        "stores": request.form.getlist('stores'),
        "notified": False
    }

    # Save alert
    with open(ALERTS_FILE, 'r') as f:
        alerts = json.load(f)
    alerts.append(alert)
    with open(ALERTS_FILE, 'w') as f:
        json.dump(alerts, f)

    return "<h1>✅ Success!</h1><p>We’ll monitor this product across selected stores and email you when the price drops.</p>"

if __name__ == '__main__':
    app.run(debug=True)
