import requests
from bs4 import BeautifulSoup
import json
import re
import time
import random
from pymongo import MongoClient, UpdateOne
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from fake_useragent import UserAgent
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

load_dotenv()

# ===== CONFIGURATION =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/infinite_craft_website?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = "infinite_craft_website"
MAX_WORKERS = 3
BATCH_SIZE = 20
SEED_ELEMENTS = ["Water", "Fire", "Wind", "Earth"]

# ===== MongoDB Setup =====
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db["recipes"]
elements_coll = db["elements"]
progress_coll = db["_progress"]

# Indexes
recipes_coll.create_index([("result", 1)])
recipes_coll.create_index([("first", 1), ("second", 1)])
elements_coll.create_index([("name", 1)], unique=True)

ua = UserAgent()

# ===== PLAYWRIGHT BROWSER =====
_browser = None

def get_browser():
    global _browser
    if not _browser:
        p = sync_playwright().start()
        _browser = p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
        )
    return _browser

def safe_str(val):
    if val is None: return ""
    if isinstance(val, (int, float)):
        return str(int(val)) if val == int(val) else str(val)
    return str(val).strip()

def extract_name_parts(text):
    text = safe_str(text)
    if not text: return ("", "")
    
    emoji_match = re.match(r'([\U0001F000-\U0010FFFF\u00A9\u00AE\u2122\u2600-\u27BF\u2300-\u23FF\u25A0-\u25FF\u2B05-\u2B55\u2934\u2935\u3030\u303D\u3297\u3299\u200D\uFE0F\u20E3]+)\s*(.*)', text)
    if emoji_match and emoji_match.group(1):
        return (emoji_match.group(1).strip(), emoji_match.group(2).strip() or text)
    return ("", text)

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
    }

def scrape_with_requests(element_name):
    """Step 1: Try with requests - fast path"""
    element_name = safe_str(element_name)
    if not element_name: return None
    
    url_name = quote(element_name.replace(' ', '-'))
    url = f"https://infinitecraftrecipe.com/recipes/{url_name}"
    
    try:
        time.sleep(1.5 + random.uniform(0, 2))
        resp = requests.get(url, headers=get_headers(), timeout=15)
        if resp.status_code != 200: return None
    except:
        return None
    
    soup = BeautifulSoup(resp.text, 'lxml')
    
    # Check for server-rendered table (some pages have it)
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cells = row.find_all(['td', 'th'])
            row_text = row.get_text(strip=True)
            if '+' in row_text and '=' in row_text and len(cells) >= 3:
                return soup  # Found actual recipe table!
    
    # Check __NEXT_DATA__
    script = soup.find('script', id='__NEXT_DATA__')
    if script:
        try:
            nd = json.loads(script.string)
            if nd.get('props', {}).get('pageProps', {}).get('recipes'):
                return soup
        except:
            pass
    
    return None  # No data found - need browser

def scrape_with_browser(element_name):
    """Step 2: Use Playwright for JS-rendered content"""
    element_name = safe_str(element_name)
    if not element_name: return None
    
    url_name = quote(element_name.replace(' ', '-'))
    url = f"https://infinitecraftrecipe.com/recipes/{url_name}"
    
    print(f"  🌐 [Browser] {element_name}", end=" ", flush=True)
    
    try:
        browser = get_browser()
        context = browser.new_context(
            user_agent=ua.random,
            viewport={'width': 1920, 'height': 1080}
        )
        page = context.new_page()
        
        # Wait for content to render
        page.goto(url, wait_until='networkidle', timeout=30000)
        time.sleep(2)  # Extra wait for JS rendering
        
        # Get FULL rendered HTML
        html = page.content()
        context.close()
        
        soup = BeautifulSoup(html, 'lxml')
        
        # Check for tables in rendered HTML
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                row_text = row.get_text(strip=True)
                if '+' in row_text and '=' in row_text:
                    print(f"✅ (rendered)")
                    return soup
        
        print(f"⚠️ (no recipes)")
        return None
        
    except Exception as e:
        print(f"❌ ({str(e)[:30]})")
        return None

def parse_recipes_from_soup(soup, element_name):
    """Extract recipes from BeautifulSoup object"""
    recipes_found = []
    element_emoji = ""
    element_name_clean = element_name
    
    # Get element info
    h1 = soup.find('h1')
    if h1:
        h1_text = h1.get_text(strip=True)
        m = re.match(r'How to make (.+?) (.+) in Infinite Craft', h1_text)
        if m:
            element_emoji = m.group(1)
            element_name_clean = m.group(2)
    
    title = soup.find('title')
    if not element_emoji and title:
        m = re.match(r'How to make (.+?) (.+) in Infinite Craft', title.get_text())
        if m:
            element_emoji = m.group(1)
    
    # Parse tables
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            row_text = row.get_text(strip=True)
            if not row_text or '+' not in row_text or '=' not in row_text:
                continue
            
            parts = re.split(r'\s*\+\s*|\s*=\s*', row_text)
            parts = [p.strip() for p in parts if p.strip()]
            
            if len(parts) >= 3:
                f_emoji, f_name = extract_name_parts(parts[0])
                s_emoji, s_name = extract_name_parts(parts[1])
                r_emoji, r_name = extract_name_parts(parts[-1])
                
                if f_name and s_name and r_name:
                    recipes_found.append({
                        "first": f_name, "first_emoji": f_emoji,
                        "second": s_name, "second_emoji": s_emoji,
                        "result": r_name, "result_emoji": r_emoji,
                    })
    
    # Parse links (backup)
    if not recipes_found:
        links = soup.find_all('a')
        for link in links:
            parent = link.parent
            if not parent: continue
            parent_text = parent.get_text(strip=True)
            if '+' in parent_text and '=' in parent_text:
                parts = re.split(r'\s*\+\s*|\s*=\s*', parent_text)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 3:
                    f_emoji, f_name = extract_name_parts(parts[0])
                    s_emoji, s_name = extract_name_parts(parts[1])
                    r_emoji, r_name = extract_name_parts(parts[-1])
                    if f_name and s_name and r_name:
                        recipes_found.append({
                            "first": f_name, "first_emoji": f_emoji,
                            "second": s_name, "second_emoji": s_emoji,
                            "result": r_name, "result_emoji": r_emoji,
                        })
    
    # Deduplicate
    seen = set()
    unique = []
    for r in recipes_found:
        key = (r['first'].lower(), r['second'].lower(), r['result'].lower())
        if key not in seen:
            seen.add(key)
            unique.append(r)
    
    return element_emoji, element_name_clean, unique

def scrape_element(element_name):
    """Master function - tries requests first, falls back to browser"""
    element_name = safe_str(element_name)
    if not element_name: return None
    
    print(f"  🔍 [{element_name}]", end=" ", flush=True)
    
    # Try fast path first
    soup = scrape_with_requests(element_name)
    
    # If no data found, use browser
    if not soup:
        soup = scrape_with_browser(element_name)
    
    if not soup:
        print("❌")
        return None
    
    # Parse recipes
    element_emoji, element_name_clean, recipes_found = parse_recipes_from_soup(soup, element_name)
    
    if recipes_found:
        print(f"✅ {len(recipes_found)} recipes 🏷️ '{element_emoji}'")
    else:
        print("⚠️ 0 recipes")
    
    return {
        "element": {"emoji": element_emoji, "name": element_name_clean or element_name},
        "recipes": recipes_found
    }

def save_results(data):
    if not data: return set()
    new_names = set()
    
    if data.get('element') and data['element'].get('name'):
        try:
            elements_coll.update_one(
                {"name": data['element']['name']},
                {"$set": {"name": data['element']['name'], "emoji": data['element'].get('emoji', ''), "last_scraped": time.time()}},
                upsert=True
            )
        except: pass
    
    for r in data.get('recipes', []):
        try:
            new_names.update([r['first'], r['second'], r['result']])
            recipes_coll.update_one(
                {"first": r['first'], "second": r['second'], "result": r['result']},
                {"$set": r}, upsert=True
            )
        except: pass
    
    return new_names

def get_known_elements():
    names = set()
    for doc in elements_coll.find({}, {"name": 1}):
        if doc.get('name'): names.add(doc['name'])
    
    pipeline = [{"$group": {"_id": None, "all": {"$addToSet": "$first"}, "all2": {"$addToSet": "$second"}, "all3": {"$addToSet": "$result"}}}]
    result = list(recipes_coll.aggregate(pipeline))
    if result:
        for key in ['all', 'all2', 'all3']:
            for name in result[0].get(key, []):
                if isinstance(name, str) and name.strip(): names.add(name.strip())
    return names

def get_scraped_set():
    doc = progress_coll.find_one({"_id": "scraped"})
    return set(doc.get("names", [])) if doc else set()

def save_scraped_set(scraped):
    progress_coll.update_one(
        {"_id": "scraped"},
        {"$set": {"names": list(scraped), "count": len(scraped), "updated": time.time()}},
        upsert=True
    )

def main():
    print("=" * 60)
    print("🔬 INFINITE CRAFT WEBSITE SCRAPER v3")
    print("📁 DB: infinite_craft_website")
    print("🌐 Playwright + Requests Hybrid")
    print("=" * 60)
    
    scraped = get_scraped_set()
    
    print(f"\n📊 Status:")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Scraped pages: {len(scraped)}")
    
    # Seed
    print("\n📥 Seeding basics...")
    initial_known = get_known_elements()
    if len(initial_known) < 10:
        for elem in SEED_ELEMENTS:
            if elem not in scraped:
                data = scrape_element(elem)
                if data:
                    n = save_results(data)
                    scraped.add(elem)
                    scraped.update(x for x in n if isinstance(x, str))
                    save_scraped_set(scraped)
    
    # Discovery loop
    print("\n🔁 Discovery loop starting...")
    iteration = 0
    
    while True:
        iteration += 1
        known = get_known_elements()
        pending = [e for e in known if e not in scraped]
        
        if not pending:
            print("\n✅ ALL DONE! No more elements to scrape.")
            break
        
        print(f"\n{'='*40}")
        print(f"📦 Round {iteration}: {len(pending)} pending, {len(known)} known")
        print(f"{'='*40}")
        
        batch = pending[:BATCH_SIZE]
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(scrape_element, e): e for e in batch}
            for future in as_completed(futures):
                try:
                    data = future.result()
                    if data:
                        new_names = save_results(data)
                        scraped.add(data['element']['name'])
                        scraped.update(n for n in new_names if isinstance(n, str) and n.strip())
                except Exception as e:
                    print(f"    ⚠️ Error: {e}")
        
        save_scraped_set(scraped)
        print(f"\n📊 Total: {len(scraped)} scraped | {recipes_coll.count_documents({})} recipes | {elements_coll.count_documents({})} elements")
        
        if iteration % 5 == 0:
            sample = list(recipes_coll.aggregate([{"$sample": {"size": 3}}]))
            for s in sample:
                print(f"   {s.get('first_emoji','')}{s.get('first','')} + {s.get('second_emoji','')}{s.get('second','')} = {s.get('result_emoji','')}{s.get('result','')}")
        
        if iteration >= 1000:  # Safety limit
            print("\n⚠️ Safety limit reached. Stopping.")
            break
    
    print("\n" + "=" * 60)
    print("🎉 COMPLETE!")
    print("=" * 60)
    print(f"\n📊 Final:")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Pages scraped: {len(scraped)}")

if __name__ == "__main__":
    main()
