import requests
from bs4 import BeautifulSoup
import re
import time
import random
from pymongo import MongoClient
from urllib.parse import quote, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from fake_useragent import UserAgent
import os
import sys
import json
from dotenv import load_dotenv

load_dotenv()

# ===== CONFIG =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "infinite_craft")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db["website_recipes"]
elements_coll = db["website_elements"]
progress_coll = db["scraping_progress"]

recipes_coll.create_index([("result", 1)])
recipes_coll.create_index([("first", 1), ("second", 1)])
elements_coll.create_index([("name", 1)], unique=True)

ua = UserAgent()
BASE = "https://infinitecraftrecipe.com"

START_TIME = time.time()
TIMEOUT = 2400

NAMES_CACHE_FILE = "element_names_cache.json"

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
    }

def fetch(url, retries=5):
    for a in range(retries):
        try:
            time.sleep(1.5 + random.uniform(0, 1.5))
            r = requests.get(url, headers=get_headers(), timeout=30)
            if r.status_code == 200: return r
            if r.status_code == 429:
                wait = (a + 1) * 15
                print(f"⏳ Rate limited, waiting {wait}s...")
                time.sleep(wait)
            if r.status_code == 404: return None
        except Exception as e:
            print(f"  ⚠️ Request error: {e}")
            if a < retries - 1: time.sleep(5)
    return None

def extract_emoji_prefix(text):
    if not text: return "", ""
    text = text.strip()
    emoji_match = re.match(r'^([\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]+\s*)(.*)', text)
    if emoji_match:
        return emoji_match.group(1).strip(), emoji_match.group(2).strip()
    return "", text

def clean_element_name(name):
    _, clean = extract_emoji_prefix(name)
    if not clean:
        clean = name
    clean = re.sub(r'[\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039]', '', clean).strip()
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def scrape_recipes_from_page(soup):
    recipes = []
    seen = set()
    
    body = soup.find('body')
    if not body: return recipes
    
    body_text = body.get_text(separator='\n')
    lines = body_text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or len(line) > 300:
            continue
        
        if '+' not in line or '=' not in line:
            continue
        
        parts = line.split('=')
        if len(parts) != 2:
            continue
        
        left = parts[0].strip()
        right = parts[1].strip()
        
        plus_count = left.count('+')
        if plus_count != 1:
            continue
        
        plus_parts = left.split('+')
        if len(plus_parts) != 2:
            continue
        
        first_raw = plus_parts[0].strip()
        second_raw = plus_parts[1].strip()
        
        f_emoji, f_name = extract_emoji_prefix(first_raw)
        s_emoji, s_name = extract_emoji_prefix(second_raw)
        
        if not f_name and first_raw:
            continue
        if not s_name and second_raw:
            continue
        
        r_emoji, r_name = extract_emoji_prefix(right)
        if not r_name and right:
            continue
        
        f_name = f_name.strip()
        s_name = s_name.strip()
        r_name = r_name.strip()
        
        if not f_name or not s_name or not r_name:
            continue
        
        if len(f_name) > 80 or len(s_name) > 80 or len(r_name) > 80:
            continue
        
        if not f_name[0].isalpha() or not s_name[0].isalpha() or not r_name[0].isalpha():
            continue
        
        key = (f_name.lower(), s_name.lower(), r_name.lower())
        if key not in seen:
            seen.add(key)
            recipes.append({
                "first": f_name, "first_emoji": f_emoji,
                "second": s_name, "second_emoji": s_emoji,
                "result": r_name, "result_emoji": r_emoji
            })
    
    return recipes

def scrape_element(name):
    if not name or not name.strip():
        return None
    name = name.strip()
    
    clean_name = clean_element_name(name)
    if not clean_name:
        print(f"  ⚠️ {name[:30]} -> empty after cleaning")
        return None
    
    url_name = quote(clean_name.replace(' ', '-'))
    url = f"{BASE}/recipes/{url_name}"
    
    print(f"  🔍 {clean_name[:35]}", end="", flush=True)
    resp = fetch(url)
    if not resp:
        print(" ❌", flush=True)
        return None
    
    soup = BeautifulSoup(resp.text, 'lxml')
    recipes = scrape_recipes_from_page(soup)
    
    elem_emoji = ""
    title_tag = soup.find('title')
    if title_tag:
        t = title_tag.get_text()
        m = re.match(r'How to make (.+?) in Infinite Craft', t)
        if m:
            e_emoji, _ = extract_emoji_prefix(m.group(1).strip())
            elem_emoji = e_emoji
    
    if len(recipes) > 0:
        print(f" ✅ {len(recipes)} recipes", flush=True)
    else:
        print(f" ⚠️ 0 recipes", flush=True)
    
    return {
        "element": {"name": clean_name, "emoji": elem_emoji, "url": url},
        "recipes": recipes
    }

def save(data):
    if not data:
        return set()
    new = set()
    if data.get('element'):
        try:
            elements_coll.update_one({"name": data['element']['name']}, {"$set": data['element']}, upsert=True)
        except:
            pass
    for r in data.get('recipes', []):
        new.update([r['first'], r['second'], r['result']])
        try:
            recipes_coll.update_one(
                {"result": r['result'], "first": r['first'], "second": r['second']},
                {"$set": r},
                upsert=True
            )
        except:
            pass
    return new

def load_progress():
    d = progress_coll.find_one({"_id": "p"})
    s = set(d.get("e", [])) if d else set()
    return s

def save_progress(s):
    progress_coll.update_one({"_id": "p"}, {"$set": {"e": list(s)}}, upsert=True)

def load_element_names():
    """Load names from cache file if exists, else from DB"""
    if os.path.exists(NAMES_CACHE_FILE):
        print("📥 Loading elements from cache file...", flush=True)
        with open(NAMES_CACHE_FILE, "r") as f:
            names = set(json.load(f))
        print(f"   ✅ {len(names)} names loaded from cache", flush=True)
        return names
    
    print("📥 Loading elements from DB in pages...", flush=True)
    raw_names = set()
    page_size = 20000
    last_id = None
    total = 0
    
    while True:
        if last_id is None:
            docs = list(elements_coll.find({}, {"name": 1, "_id": 1})
                        .sort("_id", 1)
                        .limit(page_size))
        else:
            docs = list(elements_coll.find({"_id": {"$gt": last_id}}, {"name": 1, "_id": 1})
                        .sort("_id", 1)
                        .limit(page_size))
        
        if not docs:
            break
        
        for doc in docs:
            name = doc.get("name", "")
            if name:
                raw_names.add(name)
            last_id = doc["_id"]
        
        total += len(docs)
        print(f"   ... {total} loaded", flush=True)
    
    # Save to cache file
    print("   💾 Saving to cache file...", flush=True)
    with open(NAMES_CACHE_FILE, "w") as f:
        json.dump(list(raw_names), f)
    print(f"   ✅ {len(raw_names)} names loaded and cached", flush=True)
    return raw_names

def main():
    print("=" * 60, flush=True)
    print("🔥 INFINITE CRAFT - FAST RECIPE SCRAPE (FIXED)", flush=True)
    print("=" * 60, flush=True)
    
    existing_elements = elements_coll.count_documents({})
    existing_recipes = recipes_coll.count_documents({})
    scraped = load_progress()
    
    print(f"\n📊 DB: {existing_elements} elements | {existing_recipes} recipes | {len(scraped)} scraped", flush=True)
    
    if existing_elements > 100:
        print(f"✅ Direct Phase 3: Scraping recipes...", flush=True)
        
        # Load names (from cache or DB)
        raw_names = load_element_names()
        
        # Clean names
        elem_list = []
        for name in raw_names:
            clean_name = clean_element_name(name)
            if clean_name and clean_name not in elem_list:
                elem_list.append(clean_name)
        
        # Add basics if missing
        for b in ["Water", "Fire", "Wind", "Earth"]:
            if b not in elem_list:
                elem_list.insert(0, b)
        
        pending = [e for e in elem_list if e not in scraped]
        
        print(f"\n📥 Phase 3: {len(pending)} pending / {len(elem_list)} total", flush=True)
    else:
        elements = {}
        print("\n📥 Phase 1: Quick elements fetch...", flush=True)
        for page in range(1, 7):
            url = f"{BASE}/decks" if page == 1 else f"{BASE}/decks?page={page}"
            resp = fetch(url)
            if not resp: break
            soup = BeautifulSoup(resp.text, 'lxml')
            for link in soup.find_all('a', href=True):
                if '/deck/' in link['href']:
                    d_url = link['href'] if link['href'].startswith('http') else f"{BASE}{link['href']}"
                    resp2 = fetch(d_url)
                    if not resp2: continue
                    soup2 = BeautifulSoup(resp2.text, 'lxml')
                    for l2 in soup2.find_all('a', href=True):
                        if '/recipes/' in l2['href'] and 'login' not in l2['href'].lower():
                            txt = l2.get_text(strip=True)
                            em, nm = extract_emoji_prefix(txt)
                            if not nm:
                                nm = unquote(l2['href'].split('/recipes/')[-1]).replace('-', ' ')
                            if nm and nm not in elements:
                                elements[nm] = em
            print(f"   Page {page}: {len(elements)} so far", flush=True)
        
        for nm, em in elements.items():
            try:
                elements_coll.update_one({"name": nm}, {"$set": {"name": nm, "emoji": em}}, upsert=True)
            except: pass
        
        print(f"✅ {len(elements)} elements from decks", flush=True)
        elem_list = list(elements.keys())
        pending = [e for e in elem_list if e not in scraped]
    
    print(f"\n{'='*60}", flush=True)
    print(f"🚀 STARTING RECIPE SCRAPE: {len(pending)} elements", flush=True)
    print(f"{'='*60}", flush=True)
    
    batch_num = 0
    total_start = time.time()
    
    while pending:
        elapsed = time.time() - START_TIME
        if elapsed > TIMEOUT:
            print(f"\n⏰ Timeout. Saving progress ({len(scraped)} done)...", flush=True)
            save_progress(scraped)
            print("✅ Will continue on restart", flush=True)
            sys.exit(0)
        
        batch_num += 1
        batch = pending[:BATCH_SIZE]
        
        print(f"\n📦 Batch {batch_num}: {len(batch)} elements", flush=True)
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(scrape_element, e): e for e in batch}
            for f in as_completed(futures):
                try:
                    data = f.result()
                    if data:
                        new = save(data)
                        scraped.add(data['element']['name'])
                        for n in new:
                            if n not in elem_list:
                                elem_list.append(n)
                                pending.append(n)
                except Exception as e:
                    pass
        
        save_progress(scraped)
        
        te = elements_coll.count_documents({})
        tr = recipes_coll.count_documents({})
        pending = [e for e in elem_list if e not in scraped]
        
        elapsed_total = time.time() - total_start
        rate = len(scraped) / (elapsed_total / 60) if elapsed_total > 0 else 0
        eta = (len(pending) / rate) if rate > 0 else 0
        
        print(f"   📊 {len(scraped)} done | {tr} recipes | {te} elements | {len(pending)} left | ~{int(eta)} min", flush=True)
    
    print("\n" + "=" * 60, flush=True)
    print("🎉 COMPLETE!", flush=True)
    print(f"   Elements: {elements_coll.count_documents({})}", flush=True)
    print(f"   Recipes: {recipes_coll.count_documents({})}", flush=True)

if __name__ == "__main__":
    main()
