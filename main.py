import requests
from bs4 import BeautifulSoup
import json
import re
import time
import random
from pymongo import MongoClient, UpdateOne
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from fake_useragent import UserAgent
import os
from dotenv import load_dotenv
import math

load_dotenv()

# ===== CONFIGURATION =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/infinite_craft_website?retryWrites=true&w=majority&appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "infinite_craft_website")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))  # Keep low to avoid rate limiting
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))

# ===== MongoDB Setup - NAYA DB =====
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

# ===== SEED ELEMENTS =====
SEED_ELEMENTS = ["Water", "Fire", "Wind", "Earth"]

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
    }

def safe_str(val):
    """Convert any value to string safely"""
    if val is None:
        return ""
    if isinstance(val, (int, float)):
        return str(int(val)) if val == int(val) else str(val)
    return str(val).strip()

def make_request(url, retries=5):
    """Make HTTP request with retries and rate limiting"""
    for attempt in range(retries):
        try:
            delay = REQUEST_DELAY + random.uniform(0.5, 2.0)
            time.sleep(delay)
            
            resp = requests.get(url, headers=get_headers(), timeout=30)
            
            if resp.status_code == 429:
                wait = min((attempt + 1) * 20, 120)
                print(f"  ⚠️ Rate limited! Waiting {wait}s...")
                time.sleep(wait)
                continue
            
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404:
                return None
                
            print(f"  ⚠️ Status {resp.status_code}, attempt {attempt+1}")
            
        except Exception as e:
            print(f"  ⚠️ Error: {e}, attempt {attempt+1}")
            time.sleep(10)
    
    return None

def extract_name_parts(text):
    """Extract (emoji, name) from text like '👨 Human' or '67' or '📱iPhone15'"""
    text = safe_str(text)
    if not text:
        return ("", "")
    
    # Try to match emoji at start
    # This pattern covers most emojis including skin tones, flags, etc.
    emoji_match = re.match(r'([\U0001F000-\U0010FFFF\u00A9\u00AE\u2122\u2600-\u27BF\u2300-\u23FF\u25A0-\u25FF\u2B05-\u2B55\u2934\u2935\u3030\u303D\u3297\u3299\u200D\uFE0F\u20E3]+)\s*(.*)', text)
    
    if emoji_match and emoji_match.group(1):
        emoji = emoji_match.group(1).strip()
        name = emoji_match.group(2).strip()
        if not name:
            name = text
        return (emoji, name)
    
    # No emoji found, return full text as name
    return ("", text)

def scrape_element(element_name):
    """Scrape a single element's recipe page"""
    element_name = safe_str(element_name)
    if not element_name:
        return None
    
    url_name = element_name.replace(' ', '-')
    url = f"https://infinitecraftrecipe.com/recipes/{quote(url_name)}"
    
    print(f"  🔍 [{element_name}]", end=" ", flush=True)
    
    resp = make_request(url)
    if not resp:
        print("❌")
        return None
    
    soup = BeautifulSoup(resp.text, 'lxml')
    recipes_found = []
    
    # ---- METHOD 1: Look for __NEXT_DATA__ ----
    script = soup.find('script', id='__NEXT_DATA__')
    if script:
        try:
            nd = json.loads(script.string)
            props = nd.get('props', {}).get('pageProps', {})
            
            # Check for recipe list
            if 'recipes' in props and isinstance(props['recipes'], list):
                for r in props['recipes']:
                    if isinstance(r, dict):
                        first_emoji, first_name = extract_name_parts(r.get('first', ''))
                        second_emoji, second_name = extract_name_parts(r.get('second', ''))
                        result_emoji, result_name = extract_name_parts(r.get('result', ''))
                        
                        if first_name and second_name and result_name:
                            recipes_found.append({
                                "first": first_name,
                                "first_emoji": first_emoji or safe_str(r.get('firstEmoji', '')),
                                "second": second_name,
                                "second_emoji": second_emoji or safe_str(r.get('secondEmoji', '')),
                                "result": result_name,
                                "result_emoji": result_emoji or safe_str(r.get('resultEmoji', '')),
                            })
        except:
            pass
    
    # ---- METHOD 2: Parse HTML tables ----
    if not recipes_found:
        tables = soup.find_all('table')
        for table in tables:
            rows = table.find_all('tr')
            for row in rows:
                row_text = row.get_text(strip=True)
                if not row_text:
                    continue
                
                # Split by + and =
                parts = re.split(r'\s*\+\s*|\s*=\s*', row_text)
                parts = [p.strip() for p in parts if p.strip()]
                
                if len(parts) >= 3:
                    first_emoji, first_name = extract_name_parts(parts[0])
                    second_emoji, second_name = extract_name_parts(parts[1])
                    result_emoji, result_name = extract_name_parts(parts[-1])
                    
                    if first_name and second_name and result_name:
                        recipes_found.append({
                            "first": first_name,
                            "first_emoji": first_emoji,
                            "second": second_name,
                            "second_emoji": second_emoji,
                            "result": result_name,
                            "result_emoji": result_emoji,
                        })
    
    # ---- METHOD 3: Parse from links (the website uses this pattern heavily) ----
    if not recipes_found:
        links = soup.find_all('a')
        for link in links:
            parent = link.parent
            if not parent:
                continue
            
            parent_text = parent.get_text(strip=True)
            if '+' in parent_text and '=' in parent_text:
                parts = re.split(r'\s*\+\s*|\s*=\s*', parent_text)
                parts = [p.strip() for p in parts if p.strip()]
                
                if len(parts) >= 3:
                    first_emoji, first_name = extract_name_parts(parts[0])
                    second_emoji, second_name = extract_name_parts(parts[1])
                    result_emoji, result_name = extract_name_parts(parts[-1])
                    
                    if first_name and second_name and result_name:
                        recipes_found.append({
                            "first": first_name,
                            "first_emoji": first_emoji,
                            "second": second_name,
                            "second_emoji": second_emoji,
                            "result": result_name,
                            "result_emoji": result_emoji,
                        })
    
    # ---- METHOD 4: Look for JSON in any script tag ----
    if not recipes_found:
        for script in soup.find_all('script'):
            if not script.string:
                continue
            content = script.string
            
            # Try various JSON pattern
            for pattern in [
                r'"recipes"\s*:\s*(\[[^\]]+\])',
                r'recipes\s*=\s*(\[[^\]]+\])',
                r'"recipeData"\s*:\s*(\[[^\]]+\])',
            ]:
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        if isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    first = safe_str(item.get('first', item.get('a', '')))
                                    second = safe_str(item.get('second', item.get('b', '')))
                                    result = safe_str(item.get('result', item.get('c', '')))
                                    
                                    if first and second and result:
                                        recipes_found.append({
                                            "first": first,
                                            "first_emoji": safe_str(item.get('firstEmoji', item.get('first_emoji', ''))),
                                            "second": second,
                                            "second_emoji": safe_str(item.get('secondEmoji', item.get('second_emoji', ''))),
                                            "result": result,
                                            "result_emoji": safe_str(item.get('resultEmoji', item.get('result_emoji', ''))),
                                        })
                    except:
                        pass
    
    # Deduplicate
    seen = set()
    unique = []
    for r in recipes_found:
        key = (r['first'].lower(), r['second'].lower(), r['result'].lower())
        if key not in seen:
            seen.add(key)
            unique.append(r)
    
    # Extract element info from page
    element_emoji, element_name = extract_name_parts(element_name)
    
    # Try to get proper emoji from H1
    h1 = soup.find('h1')
    if h1:
        h1_text = h1.get_text(strip=True)
        m = re.match(r'How to make (.+?) (.+) in Infinite Craft', h1_text)
        if m:
            element_emoji = m.group(1)
            element_name = m.group(2)
    
    if unique:
        print(f"✅ {len(unique)} recipes")
    else:
        print("⚠️ No recipes found")
    
    return {
        "element": {
            "emoji": element_emoji,
            "name": element_name or element_name,
        },
        "recipes": unique,
        "total": len(unique)
    }

def save_results(data):
    """Save to MongoDB and return newly discovered element names"""
    if not data:
        return set()
    
    new_names = set()
    
    # Save element
    if data.get('element') and data['element'].get('name'):
        try:
            elements_coll.update_one(
                {"name": data['element']['name']},
                {"$set": {
                    "name": data['element']['name'],
                    "emoji": data['element'].get('emoji', ''),
                    "last_scraped": time.time()
                }},
                upsert=True
            )
        except Exception as e:
            print(f"    ⚠️ Element save error: {e}")
    
    # Save recipes and collect new names
    for r in data.get('recipes', []):
        try:
            new_names.add(r['first'])
            new_names.add(r['second'])
            new_names.add(r['result'])
            
            recipes_coll.update_one(
                {"first": r['first'], "second": r['second'], "result": r['result']},
                {"$set": r},
                upsert=True
            )
        except Exception as e:
            pass
    
    return new_names

def get_known_elements():
    """Get all known element names from DB"""
    names = set()
    
    for doc in elements_coll.find({}, {"name": 1}):
        if doc.get('name'):
            names.add(doc['name'])
    
    pipeline = [
        {"$group": {
            "_id": None,
            "all": {"$addToSet": "$first"},
            "all2": {"$addToSet": "$second"},
            "all3": {"$addToSet": "$result"}
        }}
    ]
    result = list(recipes_coll.aggregate(pipeline))
    if result:
        for key in ['all', 'all2', 'all3']:
            for name in result[0].get(key, []):
                if isinstance(name, str) and name.strip():
                    names.add(name.strip())
    
    return names

def get_scraped_set():
    """Get set of already scraped element names"""
    doc = progress_coll.find_one({"_id": "scraped"})
    if doc:
        return set(doc.get("names", []))
    return set()

def save_scraped_set(scraped):
    progress_coll.update_one(
        {"_id": "scraped"},
        {"$set": {"names": list(scraped), "count": len(scraped), "updated": time.time()}},
        upsert=True
    )

def main():
    print("=" * 60)
    print("🔬 INFINITE CRAFT WEBSITE SCRAPER")
    print(f"📁 DB: {DB_NAME}")
    print("=" * 60)
    
    scraped = get_scraped_set()
    
    print(f"\n📊 Status:")
    print(f"   Elements in DB: {elements_coll.count_documents({})}")
    print(f"   Recipes in DB: {recipes_coll.count_documents({})}")
    print(f"   Already scraped pages: {len(scraped)}")
    
    # PHASE 1: Seed with basics
    print("\n📥 PHASE 1: Seeding...")
    queue = [e for e in SEED_ELEMENTS if e not in scraped]
    
    for elem in queue:
        data = scrape_element(elem)
        if data:
            new_names = save_results(data)
            scraped.add(elem)
            scraped.update(n for n in new_names if isinstance(n, str))
            save_scraped_set(scraped)
    
    # PHASE 2: Discovery loop
    print("\n🔁 PHASE 2: Discovery loop")
    iteration = 0
    no_progress_count = 0
    
    while True:
        iteration += 1
        known = get_known_elements()
        pending = [e for e in known if e not in scraped]
        
        if not pending:
            print("\n✅ All known elements scraped!")
            break
        
        print(f"\n{'='*40}")
        print(f"📦 Iteration {iteration}: {len(pending)} pending")
        print(f"{'='*40}")
        
        batch = pending[:BATCH_SIZE]
        batch_data = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            for elem in batch:
                futures[executor.submit(scrape_element, elem)] = elem
            
            for future in as_completed(futures):
                try:
                    data = future.result()
                    if data:
                        batch_data.append(data)
                        scraped.add(data['element']['name'])
                except Exception as e:
                    print(f"    ⚠️ Thread error: {e}")
        
        # Save everything
        total_new = set()
        for data in batch_data:
            new_names = save_results(data)
            total_new.update(new_names)
        
        # Update scraped set
        for data in batch_data:
            scraped.add(data['element']['name'])
        scraped.update(n for n in total_new if isinstance(n, str))
        save_scraped_set(scraped)
        
        # Stats
        print(f"\n📊 Progress: {len(scraped)} scraped | {recipes_coll.count_documents({})} recipes | {elements_coll.count_documents({})} elements")
        
        if len(pending) == len([e for e in get_known_elements() if e not in scraped]):
            no_progress_count += 1
        else:
            no_progress_count = 0
        
        if no_progress_count >= 10:
            print("\n⚠️ No new elements discovered. Checking all known elements...")
            all_known = get_known_elements()
            pending = [e for e in all_known if e not in scraped][:50]
            if not pending:
                break
            no_progress_count = 0
        
        if iteration % 5 == 0:
            print("\n📋 Sample:")
            sample = list(recipes_coll.aggregate([{"$sample": {"size": 3}}]))
            for s in sample:
                print(f"   {s.get('first_emoji','')}{s.get('first','')} + {s.get('second_emoji','')}{s.get('second','')} = {s.get('result_emoji','')}{s.get('result','')}")
    
    # Done
    print("\n" + "=" * 60)
    print("🎉 COMPLETE!")
    print("=" * 60)
    print(f"\n📊 Final:")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Pages scraped: {len(scraped)}")

if __name__ == "__main__":
    main()
