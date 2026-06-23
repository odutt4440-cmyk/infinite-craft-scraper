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
import sys

load_dotenv()

# ===== CONFIGURATION =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "infinite_craft")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "website_recipes")  # ALAG collection! Teri existing files safe
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "30"))

# ===== MongoDB Setup - ALAG COLLECTION =====
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db[COLLECTION_NAME]  # "website_recipes" collection
elements_coll = db["website_elements"]  # "website_elements" collection
stats_coll = db["scraping_progress"]

# Indexes
recipes_coll.create_index([("result", 1)])
recipes_coll.create_index([("first", 1), ("second", 1)])
elements_coll.create_index([("name", 1)], unique=True)

ua = UserAgent()

# ===== BASIC 4 ELEMENTS - YAHI SE START =====
BASIC_ELEMENTS = ["Water", "Fire", "Wind", "Earth"]

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

def make_request(url, retries=5):
    """Make HTTP request with retries and rate limiting"""
    for attempt in range(retries):
        try:
            delay = REQUEST_DELAY + random.uniform(0.5, 2.0)
            time.sleep(delay)
            
            resp = requests.get(url, headers=get_headers(), timeout=30)
            
            if resp.status_code == 429:
                wait = (attempt + 1) * 15
                print(f"  ⚠️ Rate limited! Waiting {wait}s...")
                time.sleep(wait)
                continue
            
            if resp.status_code == 200:
                return resp
            
            if resp.status_code == 404:
                return None  # Element doesn't exist on website
                
            print(f"  ⚠️ Status {resp.status_code}, attempt {attempt+1}")
            
        except Exception as e:
            print(f"  ⚠️ Request error: {e}, attempt {attempt+1}")
            time.sleep(10)
    
    return None

def extract_emoji_and_name(text):
    """Extract emoji and name - handles all edge cases"""
    text = text.strip()
    if not text:
        return "", ""
    
    # Pattern: emoji followed by name
    # Emojis are in Unicode ranges
    emoji_pattern = re.compile(
        r'([\U0001F300-\U0010FFFF\u200D\uFE0F\u00A9\u00AE\u2122\u2600-\u27BF\u2300-\u23FF'
        r'\u25A0-\u25FF\u2B05-\u2B55\u2934\u2935\u3030\u303D\u3297\u3299'
        r'\U0001F000-\U0001FFFF\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]*)'
    )
    
    match = emoji_pattern.match(text)
    emoji = match.group(1) if match else ""
    name = text[len(emoji):].strip()
    
    if not name:
        name = text
    
    return emoji, name

def scrape_element_page(element_name):
    """Scrape a single element's recipe page - complete with ALL recipes"""
    if not element_name or not isinstance(element_name, str):
        print(f"  ⚠️ Invalid element name: {element_name}")
        return None
    
    element_name = element_name.strip()
    if not element_name:
        return None
    
    url_name = element_name.replace(' ', '-')
    url = f"https://infinitecraftrecipe.com/recipes/{quote(url_name)}"
    
    print(f"  🔍 Scraping: {element_name}")
    
    resp = make_request(url)
    if not resp:
        print(f"  ❌ Failed/Not found: {element_name}")
        return None
    
    soup = BeautifulSoup(resp.text, 'lxml')
    recipes = []
    
    # === METHOD 1: __NEXT_DATA__ (if available) ===
    script_tag = soup.find('script', id='__NEXT_DATA__')
    if script_tag:
        try:
            next_data = json.loads(script_tag.string)
            if 'props' in next_data and 'pageProps' in next_data['props']:
                props = next_data['props']['pageProps']
                if 'recipes' in props and isinstance(props['recipes'], list):
                    for r in props['recipes']:
                        f_emoji, f_name = extract_emoji_and_name(r.get('first', ''))
                        s_emoji, s_name = extract_emoji_and_name(r.get('second', ''))
                        r_emoji, r_name = extract_emoji_and_name(r.get('result', ''))
                        if f_name and s_name and r_name:
                            recipes.append({
                                "first": f_name,
                                "first_emoji": f_emoji or r.get('firstEmoji', ''),
                                "second": s_name,
                                "second_emoji": s_emoji or r.get('secondEmoji', ''),
                                "result": r_name,
                                "result_emoji": r_emoji or r.get('resultEmoji', ''),
                            })
        except:
            pass
    
    # === METHOD 2: HTML Tables ===
    if not recipes:
        # Find all recipe tables
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = row.find_all(['td', 'th'])
                # Remove empty cells
                cells = [c for c in cells if c.get_text(strip=True)]
                
                if len(cells) >= 3:
                    row_text = row.get_text(strip=True)
                    
                    # Try various separator patterns
                    for sep_pattern in [r'\s*\+\s*', r'\s*=\s*']:
                        parts = re.split(sep_pattern, row_text)
                        if len(parts) >= 3:
                            first_part = parts[0].strip()
                            second_part = parts[1].strip()
                            result_part = parts[-1].strip()
                            
                            f_emoji, f_name = extract_emoji_and_name(first_part)
                            s_emoji, s_name = extract_emoji_and_name(second_part)
                            r_emoji, r_name = extract_emoji_and_name(result_part)
                            
                            if f_name and s_name and r_name:
                                recipes.append({
                                    "first": f_name,
                                    "first_emoji": f_emoji,
                                    "second": s_name,
                                    "second_emoji": s_emoji,
                                    "result": r_name,
                                    "result_emoji": r_emoji,
                                })
                            break  # Found valid split
    
    # === METHOD 3: Link-based recipes (common in this website) ===
    if not recipes:
        # Look for recipe pattern in links
        for link in soup.find_all('a', href=True):
            link_text = link.get_text(strip=True)
            href = link['href']
            
            # Check if this is part of a recipe row
            parent = link.parent
            if parent:
                parent_text = parent.get_text(strip=True)
                if '+' in parent_text and '=' in parent_text:
                    # Parse the full parent text
                    parts = re.split(r'\s*[+=]\s*', parent_text)
                    if len(parts) >= 3:
                        # Find which part is our link
                        for i, part in enumerate(parts):
                            if link_text in part:
                                if i == 0:
                                    # This is first ingredient
                                    f_emoji, f_name = extract_emoji_and_name(part)
                                    s_part = parts[1].strip()
                                    r_part = parts[-1].strip()
                                    s_emoji, s_name = extract_emoji_and_name(s_part)
                                    r_emoji, r_name = extract_emoji_and_name(r_part)
                                elif i == 1:
                                    # This is second ingredient
                                    f_part = parts[0].strip()
                                    f_emoji, f_name = extract_emoji_and_name(f_part)
                                    s_emoji, s_name = extract_emoji_and_name(part)
                                    r_part = parts[-1].strip()
                                    r_emoji, r_name = extract_emoji_and_name(r_part)
                                else:
                                    # This is result
                                    f_part = parts[0].strip()
                                    f_emoji, f_name = extract_emoji_and_name(f_part)
                                    s_part = parts[1].strip()
                                    s_emoji, s_name = extract_emoji_and_name(s_part)
                                    r_emoji, r_name = extract_emoji_and_name(part)
                                
                                if f_name and s_name and r_name:
                                    recipes.append({
                                        "first": f_name,
                                        "first_emoji": f_emoji,
                                        "second": s_name,
                                        "second_emoji": s_emoji,
                                        "result": r_name,
                                        "result_emoji": r_emoji,
                                    })
                                break
    
    # === METHOD 4: Inline JavaScript ===
    if not recipes:
        for script in soup.find_all('script'):
            if script.string:
                # Look for various data patterns
                for pattern in [
                    r'recipes\s*=\s*(\[.*?\])\s*;',
                    r'const\s+recipes\s*=\s*(\[.*?\])\s*;',
                    r'var\s+recipes\s*=\s*(\[.*?\])\s*;',
                    r'"recipes":\s*(\[.*?\])',
                ]:
                    match = re.search(pattern, script.string, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            if isinstance(data, list):
                                for item in data:
                                    if isinstance(item, dict):
                                        first = item.get('first', item.get('a', ''))
                                        second = item.get('second', item.get('b', ''))
                                        result = item.get('result', item.get('c', ''))
                                        if isinstance(first, (int, float)):
                                            first = str(first)
                                        if isinstance(second, (int, float)):
                                            second = str(second)
                                        if isinstance(result, (int, float)):
                                            result = str(result)
                                        
                                        f_emoji, f_name = extract_emoji_and_name(first)
                                        s_emoji, s_name = extract_emoji_and_name(second)
                                        r_emoji, r_name = extract_emoji_and_name(result)
                                        
                                        if f_name and s_name and r_name:
                                            recipes.append({
                                                "first": f_name,
                                                "first_emoji": f_emoji or item.get('firstEmoji', item.get('first_emoji', '')),
                                                "second": s_name,
                                                "second_emoji": s_emoji or item.get('secondEmoji', item.get('second_emoji', '')),
                                                "result": r_name,
                                                "result_emoji": r_emoji or item.get('resultEmoji', item.get('result_emoji', '')),
                                            })
                        except:
                            pass
    
    # === METHOD 5: Data attributes ===
    if not recipes:
        for elem in soup.find_all(True):
            data_recipe = elem.get('data-recipe') or elem.get('data-recipes')
            if data_recipe:
                try:
                    data = json.loads(data_recipe) if isinstance(data_recipe, str) else data_recipe
                    if isinstance(data, list):
                        for item in data:
                            if isinstance(item, dict) and 'result' in item:
                                f_emoji, f_name = extract_emoji_and_name(str(item.get('first', '')))
                                s_emoji, s_name = extract_emoji_and_name(str(item.get('second', '')))
                                r_emoji, r_name = extract_emoji_and_name(str(item.get('result', '')))
                                if f_name and s_name and r_name:
                                    recipes.append({
                                        "first": f_name,
                                        "first_emoji": f_emoji,
                                        "second": s_name,
                                        "second_emoji": s_emoji,
                                        "result": r_name,
                                        "result_emoji": r_emoji,
                                    })
                except:
                    pass
    
    # Deduplicate recipes
    seen = set()
    unique_recipes = []
    for r in recipes:
        key = (r['first'], r['second'], r['result'])
        if key not in seen:
            seen.add(key)
            unique_recipes.append(r)
    
    # Extract element info
    element_emoji = ""
    element_name_clean = element_name
    
    # From H1
    h1 = soup.find('h1')
    if h1:
        h1_text = h1.get_text(strip=True)
        match = re.match(r'How to make (.+) in Infinite Craft', h1_text)
        if match:
            combined = match.group(1).strip()
            e_emoji, e_name = extract_emoji_and_name(combined)
            if e_name:
                element_emoji = e_emoji
                element_name_clean = e_name
    
    # From title as fallback
    if not element_emoji:
        title_tag = soup.find('title')
        if title_tag:
            title_text = title_tag.get_text()
            match = re.match(r'How to make (.+) in Infinite Craft', title_text)
            if match:
                combined = match.group(1).strip()
                e_emoji, e_name = extract_emoji_and_name(combined)
                if e_name:
                    element_emoji = e_emoji
                    element_name_clean = e_name
    
    if unique_recipes:
        print(f"  ✅ {element_name}: {len(unique_recipes)} recipes, emoji: '{element_emoji}'")
    
    return {
        "element": {
            "emoji": element_emoji,
            "name": element_name_clean,
            "url": url
        },
        "recipes": unique_recipes
    }

def save_to_mongodb(data):
    """Save data to MongoDB"""
    if not data:
        return set()
    
    new_names = set()
    
    # Save element
    if data.get('element'):
        try:
            elements_coll.update_one(
                {"name": data['element']['name']},
                {"$set": data['element']},
                upsert=True
            )
        except Exception as e:
            print(f"  ⚠️ Error saving element: {e}")
    
    # Save recipes
    for recipe in data.get('recipes', []):
        try:
            new_names.add(recipe['first'])
            new_names.add(recipe['second'])
            new_names.add(recipe['result'])
            
            recipes_coll.update_one(
                {
                    "first": recipe['first'],
                    "second": recipe['second'],
                    "result": recipe['result']
                },
                {"$set": recipe},
                upsert=True
            )
        except Exception as e:
            print(f"  ⚠️ Error saving recipe: {e}")
    
    return new_names

def get_pending_elements(scraped_set):
    """Get elements that exist in DB but haven't been scraped yet"""
    all_elements = set()
    
    # Elements collection se
    for elem in elements_coll.find({}, {"name": 1}):
        all_elements.add(elem['name'])
    
    # Recipes collection se (first, second, result)
    pipeline = [
        {"$group": {
            "_id": None,
            "firsts": {"$addToSet": "$first"},
            "seconds": {"$addToSet": "$second"},
            "results": {"$addToSet": "$result"}
        }}
    ]
    result = list(recipes_coll.aggregate(pipeline))
    if result:
        for key in ['firsts', 'seconds', 'results']:
            for name in result[0].get(key, []):
                if isinstance(name, str) and name.strip():
                    all_elements.add(name.strip())
    
    # Remove already scraped
    pending = [e for e in all_elements if e not in scraped_set]
    pending.sort()
    return pending

def load_progress():
    """Load scraping progress from MongoDB"""
    doc = stats_coll.find_one({"_id": "progress"})
    if doc:
        scraped = set(doc.get("scraped_elements", []))
        return scraped
    return set()

def save_progress(scraped_set):
    """Save scraping progress"""
    stats_coll.update_one(
        {"_id": "progress"},
        {"$set": {"scraped_elements": list(scraped_set), "last_updated": time.time()}},
        upsert=True
    )

def main():
    print("=" * 60)
    print("🔬 INFINITE CRAFT RECIPE SCRAPER v2")
    print("=" * 60)
    
    # Load progress
    scraped_elements = load_progress()
    
    print(f"\n📊 Current DB state:")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Already scraped: {len(scraped_elements)} elements")
    
    # Phase 1: Seed with BASIC elements
    print("\n📥 PHASE 1: Seeding with basic elements...")
    basic_to_scrape = [e for e in BASIC_ELEMENTS if e not in scraped_elements]
    
    if basic_to_scrape:
        print(f"   Scraping {len(basic_to_scrape)} basic elements first...")
        for elem in basic_to_scrape:
            data = scrape_element_page(elem)
            if data:
                new_names = save_to_mongodb(data)
                scraped_elements.add(elem)
                for n in new_names:
                    if isinstance(n, str) and n.strip():
                        scraped_elements.add(n.strip())
                save_progress(scraped_elements)
                print(f"   ✅ Saved + discovered {len(new_names)} new names")
    
    # Phase 2: Discovery loop
    print("\n🔍 PHASE 2: Starting discovery loop...")
    iteration = 0
    last_new_count = 0
    stale_iterations = 0
    
    while True:
        iteration += 1
        pending = get_pending_elements(scraped_elements)
        
        if not pending:
            print("\n✅ No more elements to scrape!")
            break
        
        # Check if we're making progress
        if len(pending) == last_new_count:
            stale_iterations += 1
        else:
            stale_iterations = 0
        last_new_count = len(pending)
        
        if stale_iterations >= 5:
            print("\n⚠️ No new discoveries for 5 iterations. Trying broader approach...")
            # Try scraping all known elements regardless
            pending = list(elements_coll.find({}, {"name": 1}))
            pending = [e['name'] for e in pending if e['name'] not in scraped_elements][:100]
            if not pending:
                break
        
        print(f"\n{'='*40}")
        print(f"📦 Iteration {iteration} - {len(pending)} pending elements")
        print(f"{'='*40}")
        
        batch = pending[:BATCH_SIZE]
        batch_data = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(scrape_element_page, elem): elem for elem in batch}
            
            for future in as_completed(futures):
                try:
                    data = future.result()
                    if data:
                        batch_data.append(data)
                        scraped_elements.add(data['element']['name'])
                except Exception as e:
                    print(f"  ⚠️ Thread error: {e}")
        
        # Save batch
        for data in batch_data:
            new_names = save_to_mongodb(data)
            for n in new_names:
                if isinstance(n, str) and n.strip():
                    scraped_elements.add(n.strip())
        
        # Save progress
        save_progress(scraped_elements)
        
        # Stats
        total_recipes = recipes_coll.count_documents({})
        total_elems = elements_coll.count_documents({})
        print(f"\n📊 Progress: {len(scraped_elements)} scraped | {total_recipes} recipes | {total_elems} elements")
        
        # Every 10 iterations, show sample
        if iteration % 10 == 0:
            print("\n🔍 Sample recipes:")
            sample = list(recipes_coll.aggregate([{"$sample": {"size": 3}}]))
            for s in sample:
                print(f"   {s.get('first_emoji','')}{s.get('first','')} + {s.get('second_emoji','')}{s.get('second','')} = {s.get('result_emoji','')}{s.get('result','')}")
    
    # Final
    print("\n" + "=" * 60)
    print("🎉 SCRAPING COMPLETE!")
    print("=" * 60)
    print(f"\n📊 Final Statistics:")
    print(f"   Elements in DB: {elements_coll.count_documents({})}")
    print(f"   Recipes in DB: {recipes_coll.count_documents({})}")
    
    print("\n🔍 Sample recipes:")
    sample = list(recipes_coll.aggregate([{"$sample": {"size": 10}}]))
    for s in sample:
        print(f"   {s.get('first_emoji','')}{s.get('first','')} + {s.get('second_emoji','')}{s.get('second','')} = {s.get('result_emoji','')}{s.get('result','')}")
    
    print(f"\n✅ Data in collection '{COLLECTION_NAME}' - teri existing files safe!")

if __name__ == "__main__":
    main()
