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
from dotenv import load_dotenv

load_dotenv()

# ===== CONFIG =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "infinite_craft")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "30"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

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

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
    }

def fetch(url, retries=3):
    for a in range(retries):
        try:
            time.sleep(0.3 + random.uniform(0, 0.3))
            r = requests.get(url, headers=get_headers(), timeout=25)
            if r.status_code == 200: return r
            if r.status_code == 429: time.sleep((a+1)*5)
            if r.status_code == 404: return None
        except:
            if a < retries-1: time.sleep(3)
    return None

def extract_emoji_prefix(text):
    if not text: return "", ""
    text = text.strip()
    emoji_match = re.match(r'^([\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]+\s*)(.*)', text)
    if emoji_match:
        return emoji_match.group(1).strip(), emoji_match.group(2).strip()
    return "", text

def clean_element_name(name):
    """Remove all emojis from element name for URL building"""
    _, clean = extract_emoji_prefix(name)
    if not clean:
        clean = name
    # Remove any remaining emoji/special chars from middle/end
    clean = re.sub(r'[\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039]', '', clean).strip()
    # Remove double spaces
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def scrape_recipes_from_page(soup):
    recipes = []
    seen = set()
    
    # Get ALL text from body
    body = soup.find('body')
    if not body: return recipes
    
    body_text = body.get_text(separator='\n')
    lines = body_text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or len(line) > 300:
            continue
        
        # Must have + and = 
        if '+' not in line or '=' not in line:
            continue
        
        parts = line.split('=')
        if len(parts) != 2:
            continue
        
        left = parts[0].strip()
        right = parts[1].strip()
        
        # Left side must have exactly one +
        plus_count = left.count('+')
        if plus_count != 1:
            continue
        
        # Split by +  
        plus_parts = left.split('+')
        if len(plus_parts) != 2:
            continue
        
        first_raw = plus_parts[0].strip()
        second_raw = plus_parts[1].strip()
        
        # Extract emoji and name
        f_emoji, f_name = extract_emoji_prefix(first_raw)
        s_emoji, s_name = extract_emoji_prefix(second_raw)
        
        # If only emoji without name, use the raw text as name
        if not f_name and first_raw:
            # Could be just emoji, skip
            continue
        if not s_name and second_raw:
            continue
        
        # For result
        r_emoji, r_name = extract_emoji_prefix(right)
        if not r_name and right:
            continue
        
        # Clean and validate
        f_name = f_name.strip()
        s_name = s_name.strip()
        r_name = r_name.strip()
        
        # Basic validation - names should be at least 1 char
        if not f_name or not s_name or not r_name:
            continue
        
        # Avoid garbage lines (too short or too long names)
        if len(f_name) > 80 or len(s_name) > 80 or len(r_name) > 80:
            continue
        
        # Must start with a letter (not symbol/number)
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
    
    # CRITICAL FIX: Remove emoji from name before building URL
    clean_name = clean_element_name(name)
    if not clean_name:
        print(f"  ⚠️ {name[:30]} -> empty after cleaning")
        return None
    
    url_name = quote(clean_name.replace(' ', '-'))
    url = f"{BASE}/recipes/{url_name}"
    
    print(f"  🔍 {clean_name[:35]}", end="")
    resp = fetch(url)
    if not resp:
        print(" ❌")
        return None
    
    soup = BeautifulSoup(resp.text, 'lxml')
    recipes = scrape_recipes_from_page(soup)
    
    # Extract emoji from title
    elem_emoji = ""
    title_tag = soup.find('title')
    if title_tag:
        t = title_tag.get_text()
        m = re.match(r'How to make (.+?) in Infinite Craft', t)
        if m:
            e_emoji, _ = extract_emoji_prefix(m.group(1).strip())
            elem_emoji = e_emoji
    
    if len(recipes) > 0:
        print(f" ✅ {len(recipes)} recipes")
    else:
        print(f" ⚠️ 0 recipes")
    
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
    # Also check existing recipes in DB
    recipe_elements = set()
    for r in recipes_coll.find({}, {"result": 1}):
        recipe_elements.add(r.get("result", ""))
    s.update(recipe_elements)
    return s

def save_progress(s):
    progress_coll.update_one({"_id": "p"}, {"$set": {"e": list(s)}}, upsert=True)

def main():
    print("=" * 60)
    print("🔥 INFINITE CRAFT - FAST RECIPE SCRAPE (FIXED)")
    print("=" * 60)
    
    existing_elements = elements_coll.count_documents({})
    existing_recipes = recipes_coll.count_documents({})
    scraped = load_progress()
    
    print(f"\n📊 DB: {existing_elements} elements | {existing_recipes} recipes | {len(scraped)} scraped")
    
    if existing_elements > 100:
        print(f"✅ Direct Phase 3: Scraping recipes...")
        
        # Get all elements from DB - ONLY name field (fast)
        all_elements = elements_coll.find({}, {"name": 1})
        elem_list = []
        for e in all_elements:
            # Clean name: remove emoji for URL safety
            clean_name = clean_element_name(e["name"])
            if clean_name and clean_name not in elem_list:
                elem_list.append(clean_name)
        
        # Add basics if missing
        for b in ["Water", "Fire", "Wind", "Earth"]:
            if b not in elem_list:
                elem_list.insert(0, b)
        
        pending = [e for e in elem_list if e not in scraped]
        
        print(f"\n📥 Phase 3: {len(pending)} pending / {len(elem_list)} total")
    else:
        elements = {}
        print("\n📥 Phase 1: Quick elements fetch...")
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
            print(f"   Page {page}: {len(elements)} so far")
        
        for nm, em in elements.items():
            try:
                elements_coll.update_one({"name": nm}, {"$set": {"name": nm, "emoji": em}}, upsert=True)
            except: pass
        
        print(f"✅ {len(elements)} elements from decks")
        elem_list = list(elements.keys())
        pending = [e for e in elem_list if e not in scraped]
    
    # Phase 3: SCRAPE RECIPES NOW
    print(f"\n{'='*60}")
    print(f"🚀 STARTING RECIPE SCRAPE: {len(pending)} elements")
    print(f"{'='*60}")
    
    batch_num = 0
    total_start = time.time()
    
    while pending:
        elapsed = time.time() - START_TIME
        if elapsed > TIMEOUT:
            print(f"\n⏰ Timeout. Saving progress ({len(scraped)} done)...")
            save_progress(scraped)
            print("✅ Will continue on restart")
            sys.exit(0)
        
        batch_num += 1
        batch = pending[:BATCH_SIZE]
        
        print(f"\n📦 Batch {batch_num}: {len(batch)} elements")
        
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
        
        print(f"   📊 {len(scraped)} done | {tr} recipes | {te} elements | {len(pending)} left | ~{int(eta)} min")
    
    print("\n" + "=" * 60)
    print("🎉 COMPLETE!")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")

if __name__ == "__main__":
    main()
