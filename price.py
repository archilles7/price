import os
import json
import re
import time
import logging
from datetime import datetime, timedelta
from threading import Thread, Lock
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template_string, request, jsonify

app = Flask(__name__)

# === CONFIGURATION ===
ALERTS_FILE = "alerts.json"
LOG_FILE = "price_tracker.log"
RATE_LIMIT_DELAY = 2  # seconds between requests to same domain
MAX_RETRIES = 3

# Discord webhook URL
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/829925867655528519/C24e1fA5ajHbno5g7qLxlFLo7Hl1d2MjjsZTaXl6drrLTEDTZHZwgm2REZWYSHG1uPAC"

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

# Thread-safe file operations
file_lock = Lock()

# Initialize alerts file
if not os.path.exists(ALERTS_FILE):
    with open(ALERTS_FILE, 'w') as f:
        json.dump([], f)

# STORES TO SUPPORT - Updated with image selectors
STORES = {
    "coles": {
        "base": "https://www.coles.com.au",
        "search": "/search?q={sku}",
        "price_selectors": [
            "span.price__value",
            ".price-dollars",
            "[data-testid='price-value']",
            ".price"
        ],
        "name_selectors": [
            "h1[data-testid='product-title']",
            ".product-name",
            "h1.product-title",
            "a[data-testid='product-link']"
        ],
        "image_selectors": [
            "img[data-testid='product-image']",
            ".product-image img",
            ".product-tile__image img",
            "img.product-image"
        ]
    },
    "woolworths": {
        "base": "https://www.woolworths.com.au",
        "search": "/shop/search/products?searchTerm={sku}",
        "price_selectors": [
            "strong.price",
            ".price-value",
            "[data-testid='price']",
            ".price"
        ],
        "name_selectors": [
            "a.product-tile--title-link",
            ".product-title",
            "h3.product-name",
            ".product-tile__title"
        ],
        "image_selectors": [
            ".product-tile__image img",
            ".product-image img",
            "img[data-testid='product-image']",
            ".tile-image img"
        ]
    },
    "amazon": {
        "base": "https://www.amazon.com.au",
        "search": "/s?k={sku}",
        "price_selectors": [
            ".a-price-whole",
            ".a-price.a-text-price.a-size-medium.apexPriceToPay",
            "span.a-price-range",
            ".a-price .a-offscreen"
        ],
        "name_selectors": [
            "h2 a.a-link-normal span",
            "span[data-component-type='s-product-image'] img",
            ".s-size-mini .s-link-style",
            "h2.a-size-mini span"
        ],
        "image_selectors": [
            ".s-image",
            "img[data-image-latency='s-product-image']",
            ".a-section img.s-image",
            ".s-product-image-container img"
        ]
    },
    "ebay": {
        "base": "https://www.ebay.com.au",
        "search": "/sch/i.html?_nkw={sku}",
        "price_selectors": [
            ".s-item__price",
            ".notranslate",
            ".s-item__detail--primary .s-item__price"
        ],
        "name_selectors": [
            ".s-item__title",
            "h3.s-item__title"
        ],
        "image_selectors": [
            ".s-item__image img",
            "img.s-item__image",
            ".s-item__wrapper img"
        ]
    },
    "jbhifi": {
        "base": "https://www.jbhifi.com.au",
        "search": "/search?q={sku}",
        "price_selectors": [
            "span[class*='PriceTag_actual']",
            "span.price",
            ".price-value",
            ".product-price",
            ".price-current"
        ],
        "name_selectors": [
            "a.product-tile__title",
            ".product-name",
            "h3.product-title",
            ".product-title a"
        ],
        "image_selectors": [
            ".product-tile__image img",
            ".product-image img",
            "img.product-img"
        ]
    },
    "officeworks": {
        "base": "https://www.officeworks.com.au",
        "search": "/shop/search?q={sku}",
        "price_selectors": [
            "span.price__value",
            ".price-current",
            ".product-price",
            ".price"
        ],
        "name_selectors": [
            "a.product-tile__title",
            ".product-name",
            ".product-title"
        ],
        "image_selectors": [
            ".product-tile__image img",
            ".product-image img",
            "img.product-img"
        ]
    },
    "harveynorman": {
        "base": "https://www.harveynorman.com.au",
        "search": "/search?q={sku}",
        "price_selectors": [
            "span.price",
            ".price-value",
            ".product-price",
            ".price-current"
        ],
        "name_selectors": [
            "h4.product-name a",
            ".product-title",
            ".product-name"
        ],
        "image_selectors": [
            ".product-image img",
            "img.product-img",
            ".product-tile__image img"
        ]
    }
}

# === UTILITY FUNCTIONS ===
def load_alerts():
    """Thread-safe alert loading"""
    with file_lock:
        try:
            with open(ALERTS_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

def save_alerts(alerts):
    """Thread-safe alert saving"""
    with file_lock:
        with open(ALERTS_FILE, 'w') as f:
            json.dump(alerts, f, indent=2)

def send_discord_notification(title, description, fields=None, color=0x00ff00):
    """Send notification via Discord webhook"""
    try:
        embed = {
            "title": title,
            "description": description,
            "color": color,
            "timestamp": datetime.utcnow().isoformat(),
            "footer": {
                "text": "Price Tracker Bot"
            }
        }
        
        if fields:
            embed["fields"] = fields
            
        payload = {
            "embeds": [embed]
        }
        
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        
        logging.info("Discord notification sent successfully")
        return True
        
    except Exception as e:
        logging.error(f"Failed to send Discord notification: {e}")
        return False

def extract_sku(url):
    """Enhanced SKU extraction with better patterns"""
    if not url:
        return None
        
    # Clean URL
    url = url.strip()
    
    # Enhanced patterns for different stores
    patterns = [
        r'/product/[^/]*-(\d{6,})',     # Coles style: /product/name-123456
        r'/(\d{7,})',                   # Any 7+ digit number (more specific)
        r'sku[-=_](\d+)',               # sku=12345, sku-12345, sku_12345
        r'p[-=_](\d+)',                 # p-12345, p=12345
        r'itemcode[=-](\d+)',           # itemcode=12345
        r'product[/-](\d+)',            # product/12345, product-12345
        r'dp/([A-Z0-9]{10})',           # Amazon ASIN
        r'/(\d{5,})[/?]',               # 5+ digits followed by / or ?
        r'item/(\d+)',                  # item/12345
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            sku = match.group(1)
            logging.info(f"Extracted SKU: {sku} from URL: {url}")
            return sku
    
    # Fallback: last sequence of digits
    match = re.search(r'(\d{4,})(?=[^/]*$)', url)
    if match:
        sku = match.group(1)
        logging.info(f"Fallback SKU extraction: {sku}")
        return sku
        
    logging.warning(f"Could not extract SKU from URL: {url}")
    return None

def clean_price_text(price_text):
    """Clean and extract price from text"""
    if not price_text:
        return None
        
    # Remove common currency symbols and whitespace
    cleaned = re.sub(r'[^\d.,]', '', price_text.replace(',', ''))
    
    # Extract first valid number
    match = re.search(r'\d+(?:\.\d{1,2})?', cleaned)
    if match:
        try:
            return float(match.group())
        except ValueError:
            return None
    return None

def get_product_info(url):
    """Get product info from original URL"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try to extract product info
        name = None
        image = None
        price = None
        
        # Generic selectors for product name
        name_selectors = [
            "h1", "h2.product-title", ".product-name", "[data-testid='product-title']",
            ".product-title", "h1.product-title", ".product-info h1"
        ]
        
        for selector in name_selectors:
            name_el = soup.select_one(selector)
            if name_el and name_el.get_text(strip=True):
                name = name_el.get_text(strip=True)[:100]
                break
        
        # Generic selectors for product image
        image_selectors = [
            "img[data-testid='product-image']", ".product-image img", 
            ".hero-image img", ".main-image img", "img.product-img",
            ".product-photos img", ".product-gallery img"
        ]
        
        for selector in image_selectors:
            img_el = soup.select_one(selector)
            if img_el and img_el.get('src'):
                image = img_el.get('src')
                if not image.startswith('http'):
                    image = urljoin(url, image)
                break
        
        # Generic selectors for price
        price_selectors = [
            ".price", ".price-value", ".price__value", "[data-testid='price']",
            ".current-price", ".sale-price", ".product-price"
        ]
        
        for selector in price_selectors:
            price_el = soup.select_one(selector)
            if price_el:
                price_text = price_el.get_text(strip=True)
                price = clean_price_text(price_text)
                if price:
                    break
        
        return {
            "name": name or "Product",
            "image": image,
            "price": price,
            "url": url
        }
        
    except Exception as e:
        logging.error(f"Error fetching product info: {e}")
        return None

def get_store_price(store_key, sku, retries=MAX_RETRIES):
    """Enhanced price fetching with image support"""
    if store_key not in STORES:
        return None, f"Unknown store: {store_key}"
        
    store = STORES[store_key]
    search_url = urljoin(store["base"], store["search"].format(sku=sku))
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    for attempt in range(retries):
        try:
            logging.info(f"Fetching price from {store_key} (attempt {attempt + 1})")
            
            response = requests.get(search_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')

            # Try multiple price selectors
            price = None
            price_text = ""
            for selector in store["price_selectors"]:
                price_el = soup.select_one(selector)
                if price_el:
                    price_text = price_el.get_text(strip=True)
                    price = clean_price_text(price_text)
                    if price:
                        break

            # Try multiple name selectors
            name = None
            for selector in store["name_selectors"]:
                name_el = soup.select_one(selector)
                if name_el:
                    name = name_el.get_text(strip=True)
                    if name and len(name) > 3:  # Basic validation
                        break
            
            # Try to get product image
            image = None
            for selector in store["image_selectors"]:
                img_el = soup.select_one(selector)
                if img_el and img_el.get('src'):
                    image = img_el.get('src')
                    if not image.startswith('http'):
                        image = urljoin(store["base"], image)
                    break

            if not price:
                logging.warning(f"No price found for {sku} at {store_key}")
                return None, f"No price found (tried: {price_text})"

            if not name:
                name = f"Product {sku}"

            result = {
                "price": price,
                "name": name[:100],  # Limit length
                "image": image,
                "url": search_url,
                "store": store_key.capitalize(),
                "last_checked": datetime.now().isoformat(),
                "available": True
            }
            
            logging.info(f"Found: {name} - ${price} at {store_key}")
            time.sleep(RATE_LIMIT_DELAY)  # Rate limiting
            return result, None

        except requests.RequestException as e:
            logging.warning(f"Request failed for {store_key} (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            continue
        except Exception as e:
            logging.error(f"Unexpected error for {store_key}: {e}")
            break

    return {
        "store": store_key.capitalize(),
        "available": False,
        "error": f"Failed after {retries} attempts"
    }, None

# === BACKGROUND MONITORING ===
def start_monitoring():
    """Enhanced monitoring with Discord notifications"""
    logging.info("Starting price monitoring service")
    
    while True:
        try:
            alerts = load_alerts()
            updated_alerts = []
            
            for alert in alerts:
                if alert.get('notified'):
                    # Check if we should reset notification after some time
                    if 'trigger_time' in alert:
                        trigger_time = datetime.fromisoformat(alert['trigger_time'])
                        if datetime.now() - trigger_time > timedelta(days=7):
                            alert['notified'] = False
                            logging.info(f"Reset notification for alert {alert.get('sku')}")
                    
                    updated_alerts.append(alert)
                    continue

                logging.info(f"Checking alert for SKU: {alert.get('sku')}")
                matched_stores = []
                
                for store in alert.get('stores', []):
                    data, error = get_store_price(store, alert['sku'])
                    if data and data.get('available'):
                        threshold = alert.get('target_price', alert['retail_price'] * (1 - alert['discount_rate']/100))
                        if data['price'] <= threshold:
                            matched_stores.append(data)
                            logging.info(f"Deal found: {data['name']} at {store} for ${data['price']}")

                if matched_stores:
                    # Send Discord notification
                    title = f"🎉 Deal Alert: Price Drop Found!"
                    description = f"Great news! We found deals for **{alert.get('sku')}** that meet your target price!"
                    
                    fields = []
                    for item in matched_stores:
                        savings = alert['retail_price'] - item['price']
                        percentage = (savings / alert['retail_price']) * 100
                        fields.append({
                            "name": f"{item['store']}",
                            "value": f"**{item['name'][:50]}...**\n💰 ${item['price']:.2f} (Save ${savings:.2f} - {percentage:.1f}% off)\n🔗 [View Deal]({item['url']})",
                            "inline": True
                        })
                    
                    if send_discord_notification(title, description, fields):
                        alert['notified'] = True
                        alert['trigger_time'] = datetime.now().isoformat()
                        alert['deals_found'] = matched_stores

                updated_alerts.append(alert)

            save_alerts(updated_alerts)
            logging.info("Price check cycle completed")
            
        except Exception as e:
            logging.error(f"Error in monitoring loop: {e}")
        
        # Sleep for 30 minutes
        time.sleep(1800)

# Start monitoring in background
Thread(target=start_monitoring, daemon=True).start()

# === HTML TEMPLATES ===
FORM_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>🛒 Multi-Store Price Tracker</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .hero { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
    .store-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }
    .store-card { border: 1px solid #ddd; padding: 15px; border-radius: 8px; text-align: center; }
    .product-preview { border: 2px dashed #ddd; padding: 20px; text-align: center; min-height: 200px; }
    .product-preview.loading { background: #f8f9fa; }
    .product-preview img { max-width: 200px; max-height: 150px; object-fit: contain; }
    .price-options { background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }
  </style>
</head>
<body>
<div class="hero py-5 text-center">
  <div class="container">
    <h1 class="display-4">🛒 Smart Price Tracker</h1>
    <p class="lead">Track prices across multiple Australian retailers and get Discord notifications when deals match your target!</p>
  </div>
</div>

<div class="container my-5">
  <div class="row">
    <div class="col-md-8 mx-auto">
      <div class="card shadow">
        <div class="card-body">
          <form method="POST" action="/submit" id="priceForm">
            <div class="mb-4">
              <label class="form-label fw-bold">Product URL</label>
              <input type="url" name="url" id="productUrl" class="form-control form-control-lg" required 
                     placeholder="https://www.coles.com.au/product/...">
              <div class="form-text">Paste any product URL from supported stores</div>
            </div>

            <!-- Product Preview Section -->
            <div id="productPreview" class="product-preview mb-4" style="display:none;">
              <div class="spinner-border text-primary" role="status" id="loadingSpinner">
                <span class="visually-hidden">Loading...</span>
              </div>
              <div id="previewContent" style="display:none;">
                <img id="previewImage" src="" alt="Product Image" class="mb-3">
                <h5 id="previewName">Product Name</h5>
                <p class="text-muted" id="previewPrice">Current Price: $0.00</p>
              </div>
            </div>

            <div class="price-options">
              <h5 class="mb-3">💰 Price Target Options</h5>
              <div class="row">
                <div class="col-md-4 mb-3">
                  <label class="form-label fw-bold">Current Retail Price ($)</label>
                  <input type="number" step="0.01" name="retail_price" id="retailPrice" class="form-control" min="0.01">
                  <div class="form-text">The regular/current price</div>
                </div>
                <div class="col-md-4 mb-3">
                  <label class="form-label fw-bold">Target Price ($)</label>
                  <input type="number" step="0.01" name="target_price" id="targetPrice" class="form-control" min="0.01">
                  <div class="form-text">Exact price you want</div>
                </div>
                <div class="col-md-4 mb-3">
                  <label class="form-label fw-bold">OR Discount Rate (%)</label>
                  <input type="number" name="discount_rate" id="discountRate" class="form-control" min="5" max="90" placeholder="20">
                  <div class="form-text">Percentage off retail</div>
                </div>
              </div>
              <div class="alert alert-info">
                <small><strong>Note:</strong> Enter either a specific target price OR a discount percentage. Target price takes priority if both are provided.</small>
              </div>
            </div>

            <div class="mb-4">
              <label class="form-label fw-bold">Discord User (Optional)</label>
              <input type="text" name="discord_user" class="form-control" placeholder="@username or user#1234">
              <div class="form-text">We'll mention you in Discord notifications</div>
            </div>

            <button type="submit" class="btn btn-primary btn-lg w-100">
              ➡️ Find Current Prices & Set Alert
            </button>
          </form>
        </div>
      </div>
    </div>
  </div>

  <div class="row mt-5">
    <div class="col-12">
      <h3 class="text-center mb-4">Supported Stores</h3>
      <div class="store-grid">
        <div class="store-card">📦 Coles</div>
        <div class="store-card">🛒 Woolworths</div>
        <div class="store-card">📱 Amazon AU</div>
        <div class="store-card">🏪 eBay AU</div>
        <div class="store-card">🎵 JB Hi-Fi</div>
        <div class="store-card">🏢 Officeworks</div>
        <div class="store-card">🖥️ Harvey Norman</div>
      </div>
    </div>
  </div>
</div>

<script>
let previewTimeout;

document.getElementById('productUrl').addEventListener('input', function() {
    const url = this.value.trim();
    const preview = document.getElementById('productPreview');
    const spinner = document.getElementById('loadingSpinner');
    const content = document.getElementById('previewContent');
    
    // Clear previous timeout
    clearTimeout(previewTimeout);
    
    if (url && url.startsWith('http')) {
        preview.style.display = 'block';
        spinner.style.display = 'block';
        content.style.display = 'none';
        
        // Debounce the API call
        previewTimeout = setTimeout(() => {
            fetch('/preview', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: 'url=' + encodeURIComponent(url)
            })
            .then(response => response.json())
            .then(data => {
                spinner.style.display = 'none';
                if (data.success) {
                    document.getElementById('previewImage').src = data.image || '';
                    document.getElementById('previewName').textContent = data.name || 'Product';
                    document.getElementById('previewPrice').textContent = data.price ? `Current Price: $${data.price}` : 'Price not available';
                    
                    // Auto-fill retail price if found
                    if (data.price && !document.getElementById('retailPrice').value) {
                        document.getElementById('retailPrice').value = data.price;
                    }
                    
                    content.style.display = 'block';
                } else {
                    content.innerHTML = '<p class="text-danger">Could not load product preview</p>';
                    content.style.display = 'block';
                }
            })
            .catch(error => {
                spinner.style.display = 'none';
                content.innerHTML = '<p class="text-danger">Error loading preview</p>';
                content.style.display = 'block';
            });
        }, 1000); // 1 second delay
    } else {
        preview.style.display = 'none';
    }
});

// Auto-calculate target price when discount rate changes
document.getElementById('discountRate').addEventListener('input', function() {
    const retailPrice = parseFloat(document.getElementById('retailPrice').value);
    const discountRate = parseFloat(this.value);
    
    if (retailPrice && discountRate) {
        const targetPrice = retailPrice * (1 - discountRate / 100);
        document.getElementById('targetPrice').value = targetPrice.toFixed(2);
    }
});

// Auto-calculate discount rate when target price changes
document.getElementById('targetPrice').addEventListener('input', function() {
    const retailPrice = parseFloat(document.getElementById('retailPrice').value);
    const targetPrice = parseFloat(this.value);
    
    if (retailPrice && targetPrice && targetPrice < retailPrice) {
        const discountRate = ((retailPrice - targetPrice) / retailPrice) * 100;
        document.getElementById('discountRate').value = Math.round(discountRate);
    }
});
</script>
</body>
</html>"""

RESULTS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Price Comparison Results</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    .price-card { transition: transform 0.2s; }
    .price-card:hover { transform: translateY(-5px); }
    .not-available { opacity: 0.6; background: #f8f9fa; }
    .best-deal { border: 3px solid #28a745 !important; position: relative; }
    .best-deal::before { 
      content: "🏆 BEST DEAL"; 
      position: absolute; 
      top: -10px; 
      left: 10px; 
      background: #28a745; 
      color: white; 
      padding: 5px 10px; 
      border-radius: 15px; 
      font-size: 12px; 
      font-weight: bold;
    }
    .product-image { width: 100%; height: 200px; object-fit: contain; background: #f8f9fa; }
    .not-available-img { width: 100%; height: 200px; background: #f8f9fa; display: flex; align-items: center; justify-content: center; }
    .alert-summary { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }
  </style>
</head>
<body class="bg-light">
<div class="container py-5">
  <!-- Alert Summary -->
  <div class="alert-summary p-4 rounded mb-4">
    <h2 class="mb-3">✅ Price Alert Set Successfully!</h2>
    <div class="row">
      <div class="col-md-6">
        <p><strong>Product:</strong> {{original_product.name}}</p>
        <p><strong>SKU/ID:</strong> {{sku}}</p>
        <p><strong>Current Retail Price:</strong> ${{retail_price}}</p>
      </div>
      <div class="col-md-6">
        <p><strong>Target Price:</strong> ${{target_price}}</p>
        <p><strong>Monitoring:</strong> {{stores|length}} stores</p>
        <p><strong>Discord Notifications:</strong> Enabled ✅</p>
      </div>
    </div>
  </div>

  <h3 class="mb-4">📊 Current Price Comparison</h3>
  
  <div class="row">
    {% for result in results %}
    <div class="col-md-6 col-lg-4 mb-4">
      <div class="card price-card h-100 {% if result.available and result.price <= target_price %}best-deal{% endif %} {% if not result.available %}not-available{% endif %}">
        <div class="card-body text-center">
          <h5 class="card-title">{{result.store}}</h5>
          
          {% if result.available %}
            {% if result.image %}
              <img src="{{result.image}}" alt="{{result.name}}" class="product-image mb-3">
            {% else %}
              <div class="not-available-img mb-3">
                <span class="text-muted">📷 No Image</span>
              </div>
            {% endif %}
            
            <h6 class="text-truncate">{{result.name}}</h6>
            <p class="h4 text-success">${{result.price}}</p>
            
            {% if result.price <= target_price %}
              <div class="alert alert-success">
                <strong>🎉 DEAL FOUND!</strong><br>
                Save ${{retail_price - result.price}} ({{((retail_price - result.price) / retail_price * 100)|round|int}}% off)
              </div>
            {% else %}
              <p class="text-muted">
                ${{result.price - target_price}} above target
              </p>
            {% endif %}
            
            <a href="{{result.url}}" target="_blank" class="btn btn-primary btn-sm">View Product</a>
          {% else %}
            <div class="not-available-img mb-3">
              <div class="text-center">
                <h1 style="font-size: 4rem; color: #dc3545;">❌</h1>
                <h5 class="text-danger">NOT AVAILABLE</h5>
              </div>
            </div>
            <p class="text-muted">Product not found at this store</p>
            {% if result.error %}
              <small class="text-muted">{{result.error}}</small>
            {% endif %}
          {% endif %}
        </div>
      </div>
    </div>
    {% endfor %}
  </div>

  <div class="text-center mt-4">
    <a href="/" class="btn btn-secondary">Set Another Alert</a>
    <a href="/status" class="btn btn-info">View All Alerts</a>
  </div>
</div>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(FORM_HTML)

@app.route('/preview', methods=['POST'])
def preview():
    """API endpoint for product preview"""
    url = request.form.get('url', '').strip()
    
    if not url:
        return jsonify({"success": False, "error": "No URL provided"})
    
    try:
        product_info = get_product_info(url)
        if product_info:
            return jsonify({
                "success": True,
                "name": product_info.get("name"),
                "image": product_info.get("image"),
                "price": product_info.get("price")
            })
        else:
            return jsonify({"success": False, "error": "Could not fetch product info"})
    except Exception as e:
        logging.error(f"Preview error: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route('/submit', methods=['POST'])
def submit():
    try:
        url = request.form.get('url', '').strip()
        retail_price = float(request.form.get('retail_price', 0))
        target_price = request.form.get('target_price', '')
        discount_rate = request.form.get('discount_rate', '')
        discord_user = request.form.get('discord_user', '').strip()

        # Validate inputs
        if not url or retail_price <= 0:
            return "<h1>❌ Missing required fields</h1>", 400
        
        # Calculate target price
        if target_price:
            target_price = float(target_price)
        elif discount_rate:
            discount_rate = float(discount_rate)
            target_price = retail_price * (1 - discount_rate/100)
        else:
            return "<h1>❌ Please specify either target price or discount rate</h1>", 400

        sku = extract_sku(url)
        if not sku:
            return """
            <div class="container mt-5">
                <div class="alert alert-danger">
                    <h4>❌ Could not extract product ID</h4>
                    <p>Make sure your URL contains a valid product identifier.</p>
                    <a href="/" class="btn btn-primary">Try Again</a>
                </div>
            </div>
            """, 400

        # Get original product info
        original_product = get_product_info(url) or {"name": "Product", "image": None}
        
        # Get current prices from all stores
        results = []
        for store_key in STORES.keys():
            result, error = get_store_price(store_key, sku)
            if result:
                results.append(result)
            else:
                results.append({
                    "store": store_key.capitalize(),
                    "available": False,
                    "error": error or "Not found"
                })
        
        # Create alert
        alert = {
            "id": f"{int(time.time())}_{sku}",
            "url": url,
            "sku": sku,
            "retail_price": retail_price,
            "target_price": target_price,
            "discord_user": discord_user,
            "stores": list(STORES.keys()),
            "notified": False,
            "created_at": datetime.now().isoformat()
        }

        alerts = load_alerts()
        alerts.append(alert)
        save_alerts(alerts)

        logging.info(f"New alert created: {alert['id']}")

        return render_template_string(
            RESULTS_HTML,
            sku=sku,
            retail_price=retail_price,
            target_price=target_price,
            results=results,
            original_product=original_product,
            stores=STORES.keys()
        )
        
    except Exception as e:
        logging.error(f"Error in submit: {e}")
        return f"<h1>❌ Error: {str(e)}</h1>", 500

@app.route('/status')
def status():
    """Status page showing all alerts"""
    alerts = load_alerts()
    active_alerts = [a for a in alerts if not a.get('notified')]
    
    return jsonify({
        "status": "running",
        "total_alerts": len(alerts),
        "active_alerts": len(active_alerts),
        "supported_stores": list(STORES.keys()),
        "discord_webhook": "configured" if DISCORD_WEBHOOK_URL else "not configured"
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "False").lower() == "true"
    
    logging.info(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
