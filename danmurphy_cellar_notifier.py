import requests
import json
import os
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from apscheduler.schedulers.blocking import BlockingScheduler

# Reliable .env loading for Docker
load_dotenv(override=True)
load_dotenv(dotenv_path="/app/.env", override=True)

PAGE_URL = "https://www.danmurphys.com.au/red-wine/cellarrelease-1"
DATA_FILE = "/data/previous_cellar_releases.json"


def log(message: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def is_in_stock(product):
    stock_text = product.get("stock_text", "").lower()
    return any(word in stock_text for word in ["add to cart", "in stock", "buy now", "delivery"]) and "out of stock" not in stock_text


def fetch_red_wines():
    log("Fetching Red Wine Cellar Release page with browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        })
        page.goto(PAGE_URL, wait_until="networkidle", timeout=90000)
        
        # Better waiting and scrolling for dynamic content
        page.wait_for_selector("div[class*='product'], div[class*='card'], article", timeout=45000)
        page.evaluate("window.scrollBy(0, 1000)")
        page.wait_for_timeout(3000)
        
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    wine_cards = soup.select("div[class*='product'], div[class*='card'], article, div[data-testid*='product'], div[class*='item']")

    red_wines = {}
    for card in wine_cards:
        try:
            # Name
            name_tag = card.select_one("h1, h2, h3, h4, a, span[class*='name'], span[class*='title'], div[class*='title']")
            name = name_tag.get_text(strip=True) if name_tag else None
            if not name or len(name) < 5:
                continue

            # URL → shortened clean version
            link_tag = card.select_one("a[href*='/product/']")
            full_url = "https://www.danmurphys.com.au" + link_tag["href"] if link_tag else PAGE_URL
            short_url = full_url.split("?")[0]   # Clean short link

            # Price
            price_tag = card.select_one("span[class*='price'], [class*='dollar'], div[class*='price']")
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            # Stock text
            stock_text = ""
            for tag in card.select("button, span[class*='stock'], span[class*='cart'], span[class*='add'], div[class*='availability']"):
                stock_text += tag.get_text(strip=True) + " "

            stockcode = full_url.split("DM_")[-1].split("/")[0] if "DM_" in full_url else f"unknown_{hash(name)}"

            red_wines[stockcode] = {
                "name": name,
                "url": short_url,
                "price": price,
                "stock_text": stock_text.strip(),
                "stockcode": stockcode
            }
        except:
            continue

    log(f"✅ Successfully loaded {len(red_wines)} red wines (Playwright render)")
    return red_wines


def send_test_with_current():
    log("=" * 80)
    log("🧪 TEST MODE: Sending notification with current in-stock wines")
    log("=" * 80)

    current = fetch_red_wines()
    in_stock = {k: v for k, v in current.items() if is_in_stock(v)}

    if not in_stock:
        log("No wines currently in stock - nothing to notify.")
        return

    log(f"Found {len(in_stock)} wines in stock. Sending test notification...")

    send_telegram_notification("TEST: Currently Available Red Cellar Release Wines", in_stock)
    send_discord_notification("TEST: Currently Available Red Cellar Release Wines", in_stock)

    log("✅ Test notification sent!")
    log("=" * 80)


def load_previous():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_current(current):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)


def send_telegram_notification(title, products):
    if not products:
        return
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        log("⚠️ Telegram credentials missing")
        return

    message = f"🍷 **{title}** ({len(products)} red wines):\n\n"
    for p in list(products.values())[:8]:
        name = p.get("name", "Unknown Wine")
        price = p.get("price", "N/A")
        url = p.get("url", PAGE_URL)
        message += f"• {name} — **{price}**\n"
        message += f"[View on Dan Murphy's]({url})\n\n"

    if len(products) > 8:
        message += f"... and {len(products) - 8} more."

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        )
        log("✅ Telegram notification sent")
    except Exception as e:
        log(f"Failed to send Telegram: {e}")


def send_discord_notification(title, products):
    if not products:
        return

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        log("⚠️ Discord webhook URL missing")
        return

    embed = {
        "title": title,
        "color": 0x9C2A2A,
        "description": f"Found {len(products)} red wines currently in stock.",
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "Dan Murphy's Red Wine Cellar Notifier"}
    }

    fields = []
    for p in list(products.values())[:8]:
        name = p.get("name", "Unknown Wine")
        price = p.get("price", "N/A")
        url = p.get("url", PAGE_URL)
        fields.append({
            "name": f"{name} — {price}",
            "value": f"[View →]({url})",
            "inline": False
        })

    if len(products) > 8:
        fields.append({
            "name": "⋯ And more",
            "value": f"+ {len(products) - 8} other wines",
            "inline": False
        })

    embed["fields"] = fields

    data = {
        "username": "Dan Murphy Wine Notifier",
        "embeds": [embed]
    }

    try:
        response = requests.post(webhook_url, json=data, timeout=10)
        if response.status_code == 204:
            log("✅ Discord notification sent")
        else:
            log(f"Failed to send Discord: {response.status_code}")
    except Exception as e:
        log(f"Failed to send Discord: {e}")


def run_check():
    log("=" * 80)
    log("Starting Dan Murphy's Red Wine Cellar Release check...")
    log("=" * 80)

    current = fetch_red_wines()
    previous = load_previous()

    in_stock_count = sum(1 for p in current.values() if is_in_stock(p))
    out_stock_count = len(current) - in_stock_count

    log(f"✅ Tracking {len(current)} red wines ({in_stock_count} in stock • {out_stock_count} out of stock)")

    new_skus = set(current.keys()) - set(previous.keys())
    new_in_stock = {sku: current[sku] for sku in new_skus if is_in_stock(current[sku])}

    restocked = {}
    for sku, prod in current.items():
        if sku in previous and not is_in_stock(previous.get(sku, {})) and is_in_stock(prod):
            restocked[sku] = prod

    if new_in_stock:
        send_telegram_notification("New Red Cellar Release wines added (in stock)", new_in_stock)
        send_discord_notification("New Red Cellar Release wines added (in stock)", new_in_stock)

    if restocked:
        send_telegram_notification("Red Cellar Release wines restocked (back in stock)", restocked)
        send_discord_notification("Red Cellar Release wines restocked (back in stock)", restocked)

    if not new_in_stock and not restocked:
        log("No new red wines or restocks found this run.")

    save_current(current)

    next_run = datetime.now() + timedelta(hours=6)
    log(f"✅ Next check scheduled at: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 80)


if __name__ == "__main__":
    log("🍷 Dan Murphy Red Wine Cellar Notifier started")
    log("→ Running immediate check on startup...")

    run_check()

    scheduler = BlockingScheduler(timezone="Australia/Brisbane")
    scheduler.add_job(run_check, 'interval', hours=6)

    log("→ Scheduler started. The notifier will now run every 6 hours.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log("Notifier stopped.")