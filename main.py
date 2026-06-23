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

load_dotenv()

# ===== CONFIGURATION =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "infinite_craft")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))  # Items to scrape before saving
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "5"))  # Threads
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.5"))  # Seconds between requests
BATCH_DELAY = float(os.getenv("BATCH_DELAY", "5"))  # Delay after each batch
STRATEGY = os.getenv("STRATEGY", "hybrid")  # hybrid, sitemap_only, or brute_force

# ===== MongoDB Setup =====
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db["recipes"]
elements_coll = db["elements"]
stats_coll = db["scraping_stats"]

# Indexes
recipes_coll.create_index([("result", 1)])
recipes_coll.create_index([("first", 1), ("second", 1)])
recipes_coll.create_index([("first", 1)])
recipes_coll.create_index([("second", 1)])
elements_coll.create_index([("name", 1)], unique=True)

ua = UserAgent()

def get_headers():
    return {
        "User-Agent": ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }

def make_request(url, retries=3):
    """Make HTTP request with retries and rotating user agents"""
    for attempt in range(retries):
        try:
            time.sleep(REQUEST_DELAY + random.uniform(0, 1))
            resp = requests.get(url, headers=get_headers(), timeout=30)
            
            if resp.status_code == 429:
                wait = (attempt + 1) * 10
                print(f"  ⚠️ Rate limited! Waiting {wait}s...")
                time.sleep(wait)
                continue
            
            if resp.status_code == 200:
                return resp
            
            print(f"  ⚠️ Status {resp.status_code} for {url}, attempt {attempt+1}")
            
        except Exception as e:
            print(f"  ⚠️ Error: {e}, attempt {attempt+1}")
            time.sleep(5)
    
    return None

def extract_emoji_and_name(text):
    """Extract emoji and name from text like '👨 Human' or '📱iPhone'"""
    # Pattern: optional emoji (multiple unicode chars) followed by name
    match = re.match(r'([\U0001F300-\U0010FFFF\u200D\uFE0F\u00A9\u00AE\u2122\u2600-\u27BF\u2300-\u23FF]*)\s*(.*)', text.strip())
    if match:
        emoji = match.group(1).strip()
        name = match.group(2).strip()
        if not emoji:
            # Try simpler emoji patterns
            emoji_match = re.match(r'([^\w\s])?(.*)', text.strip())
            if emoji_match and emoji_match.group(1):
                emoji = emoji_match.group(1)
                name = emoji_match.group(2).strip()
        return emoji, name
    return "", text.strip()

def scrape_element_page(element_name):
    """Scrape a single element's recipe page - complete with ALL recipes"""
    url_name = element_name.replace(' ', '-')
    url = f"https://infinitecraftrecipe.com/recipes/{quote(url_name)}"
    
    print(f"  🔍 Scraping: {element_name}")
    
    resp = make_request(url)
    if not resp:
        print(f"  ❌ Failed: {element_name}")
        return None
    
    soup = BeautifulSoup(resp.text, 'lxml')
    recipes = []
    
    # METHOD 1: Check for Next.js data in __NEXT_DATA__
    script_tag = soup.find('script', id='__NEXT_DATA__')
    if script_tag:
        try:
            next_data = json.loads(script_tag.string)
            # If page props contain recipe data
            if 'props' in next_data and 'pageProps' in next_data['props']:
                props = next_data['props']['pageProps']
                if 'recipes' in props:
                    for r in props['recipes']:
                        recipes.append({
                            "first": r.get('first', ''),
                            "first_emoji": r.get('firstEmoji', ''),
                            "second": r.get('second', ''),
                            "second_emoji": r.get('secondEmoji', ''),
                            "result": r.get('result', ''),
                            "result_emoji": r.get('resultEmoji', ''),
                        })
                    if recipes:
                        print(f"  ✅ Found {len(recipes)} recipes via __NEXT_DATA__")
        except:
            pass
    
    # METHOD 2: Find recipes in HTML tables/rows
    if not recipes:
        # Try multiple table structures
        for table in soup.find_all('table'):
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) >= 3:
                    # Try to extract recipe data
                    recipe_text = row.get_text(strip=True)
                    
                    # Pattern: emoji1 name1 + emoji2 name2 = emoji3 name3
                    parts = re.split(r'[+=]', recipe_text)
                    if len(parts) >= 3:
                        first = parts[0].strip()
                        second = parts[1].strip()
                        result = parts[-1].strip()
                        
                        f_emoji, f_name = extract_emoji_and_name(first)
                        s_emoji, s_name = extract_emoji_and_name(second)
                        r_emoji, r_name = extract_emoji_and_name(result)
                        
                        if f_name and s_name and r_name:
                            recipes.append({
                                "first": f_name,
                                "first_emoji": f_emoji,
                                "second": s_name,
                                "second_emoji": s_emoji,
                                "result": r_name,
                                "result_emoji": r_emoji,
                            })
        
        if recipes:
            print(f"  ✅ Found {len(recipes)} recipes via HTML parsing")
    
    # METHOD 3: Check for inline JavaScript data
    if not recipes:
        for script in soup.find_all('script'):
            if script.string:
                # Look for recipe arrays or data objects
                patterns = [
                    r'recipes\s*=\s*(\[.*?\]);',
                    r'recipeData\s*=\s*(\[.*?\]);',
                    r'data\.index\s*=\s*({.*?});',
                    r'"recipes":\s*(\[.*?\])',
                ]
                for pattern in patterns:
                    match = re.search(pattern, script.string, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            if isinstance(data, list):
                                for item in data:
                                    if isinstance(item, dict) and 'result' in item:
                                        recipes.append({
                                            "first": item.get('first', ''),
                                            "first_emoji": item.get('firstEmoji', ''),
                                            "second": item.get('second', ''),
                                            "second_emoji": item.get('secondEmoji', ''),
                                            "result": item.get('result', ''),
                                            "result_emoji": item.get('resultEmoji', ''),
                                        })
                            elif isinstance(data, dict):
                                # Handle index format
                                pass
                        except:
                            pass
    
    # Extract element info
    element_emoji = ""
    element_name_clean = element_name
    
    # From title tag
    title_tag = soup.find('title')
    if title_tag:
        title_text = title_tag.get_text()
        match = re.match(r'How to make (.+?) (.+) in Infinite Craft', title_text)
        if match:
            element_emoji = match.group(1).strip()
            element_name_clean = match.group(2).strip()
    
    # From H1 tag
    if not element_emoji:
        h1 = soup.find('h1')
        if h1:
            h1_text = h1.get_text(strip=True)
            e_emoji, e_name = extract_emoji_and_name(h1_text.replace('How to make ', '').replace(' in Infinite Craft', ''))
            element_emoji = e_emoji
            element_name_clean = e_name if e_name else element_name
    
    result = {
        "element": {
            "emoji": element_emoji,
            "name": element_name_clean,
            "url": url
        },
        "recipes": recipes,
        "total_recipes": len(recipes)
    }
    
    print(f"  ✅ {element_name}: {len(recipes)} recipes found")
    return result

def get_all_element_names_sitemap():
    """Get ALL element names from sitemap"""
    print("📥 Fetching sitemap for all elements...")
    elements = set()
    
    urls = [
        "https://infinitecraftrecipe.com/sitemap.xml",
        "https://infinitecraftrecipe.com/sitemap-0.xml",
        "https://infinitecraftrecipe.com/robots.txt",
    ]
    
    for sitemap_url in urls:
        resp = make_request(sitemap_url)
        if not resp:
            continue
        
        # Try parsing as XML
        try:
            soup = BeautifulSoup(resp.text, 'xml')
            for loc in soup.find_all('loc'):
                url_text = loc.get_text()
                match = re.search(r'/recipes/(.+?)(?:\.html)?$', url_text)
                if match:
                    element = match.group(1).replace('-', ' ').strip()
                    elements.add(element)
        except:
            # Try text parsing
            matches = re.findall(r'/recipes/([^\s<>\"]+)', resp.text)
            for m in matches:
                element = m.replace('-', ' ').replace('.html', '').strip()
                elements.add(element)
    
    print(f"  ✅ Found {len(elements)} elements from sitemap")
    return list(elements)

def scrape_popular_elements():
    """Get elements from popular recipes page"""
    print("📥 Fetching popular elements...")
    elements = set()
    
    resp = make_request("https://infinitecraftrecipe.com/popular-recipes")
    if resp:
        soup = BeautifulSoup(resp.text, 'lxml')
        for link in soup.find_all('a', href=True):
            match = re.search(r'/recipes/(.+)$', link['href'])
            if match:
                element = match.group(1).replace('-', ' ').strip()
                elements.add(element)
    
    # Also scrape recipe pages we find
    print(f"  ✅ Found {len(elements)} popular elements")
    return list(elements)

def extract_elements_from_recipes():
    """Extract new element names from already scraped recipes"""
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
        all_names = set(result[0]['firsts'] + result[0]['seconds'] + result[0]['results'])
        return [n for n in all_names if n]
    return []

def save_batch_to_mongodb(data_batch):
    """Save a batch of scraped data to MongoDB"""
    if not data_batch:
        return
    
    element_ops = []
    recipe_ops = []
    new_names = set()
    
    for data in data_batch:
        if data.get('element'):
            element_ops.append(UpdateOne(
                {"name": data['element']['name']},
                {"$set": data['element']},
                upsert=True
            ))
        
        for recipe in data.get('recipes', []):
            # Create a unique key based on sorted first/second
            first = recipe['first']
            second = recipe['second']
            
            # Add ingredient names to our tracking set
            new_names.add(first)
            new_names.add(second)
            new_names.add(recipe['result'])
            
            recipe_ops.append(UpdateOne(
                {
                    "first": first,
                    "second": second,
                    "result": recipe['result']
                },
                {"$set": recipe},
                upsert=True
            ))
    
    if element_ops:
        result = elements_coll.bulk_write(element_ops, ordered=False)
        print(f"  💾 Elements upserted: {result.upserted_count}")
    
    if recipe_ops:
        result = recipes_coll.bulk_write(recipe_ops, ordered=False)
        print(f"  💾 Recipes upserted: {result.upserted_count}")
    
    return list(new_names)

def discover_new_elements(data_batch, existing_elements):
    """Extract new element names from recipes that we haven't scraped yet"""
    new_ingredients = set()
    for data in data_batch:
        for recipe in data.get('recipes', []):
            new_ingredients.add(recipe['first'])
            new_ingredients.add(recipe['second'])
            new_ingredients.add(recipe['result'])
    
    return [n for n in new_ingredients if n not in existing_elements]

def update_scraping_stats(stats):
    """Update scraping progress in MongoDB"""
    stats_coll.update_one(
        {"_id": "progress"},
        {"$set": stats},
        upsert=True
    )

def get_scraping_progress():
    """Get current scraping progress"""
    doc = stats_coll.find_one({"_id": "progress"})
    if doc:
        return {
            "scraped": doc.get("scraped_elements", set()),
            "pending": doc.get("pending_elements", []),
            "total_recipes": doc.get("total_recipes", 0),
            "total_elements": doc.get("total_elements", 0),
        }
    return {"scraped": set(), "pending": [], "total_recipes": 0, "total_elements": 0}

def main():
    print("=" * 60)
    print("🔬 INFINITE CRAFT RECIPE SCRAPER")
    print("=" * 60)
    
    # Get current progress (for resuming)
    progress = get_scraping_progress()
    scraped_elements = progress["scraped"]
    
    if not isinstance(scraped_elements, set):
        scraped_elements = set(scraped_elements)
    
    print(f"\n📊 Current DB state:")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Already scraped: {len(scraped_elements)} elements")
    
    # Phase 1: Get all known elements
    print("\n📥 PHASE 1: Collecting all element names...")
    all_elements = []
    
    # Get from sitemap
    sitemap_elements = get_all_element_names_sitemap()
    all_elements.extend(sitemap_elements)
    
    # Get from popular
    popular = scrape_popular_elements()
    all_elements.extend(popular)
    
    # Get existing scraped elements
    existing_elems = list(elements_coll.find({}, {"name": 1}))
    all_elements.extend([e["name"] for e in existing_elems])
    
    # Get elements from already saved recipes
    recipe_elements = extract_elements_from_recipes()
    all_elements.extend(recipe_elements)
    
    # Deduplicate
    all_elements = list(set(all_elements))
    print(f"\n   Total unique elements to scrape: {len(all_elements)}")
    
    # Filter out already scraped
    pending = [e for e in all_elements if e not in scraped_elements]
    print(f"   Already scraped: {len(scraped_elements)}")
    print(f"   Remaining to scrape: {len(pending)}")
    
    # Phase 2: Scrape with discovery loop
    print("\n🔍 PHASE 2: Scraping elements with discovery...")
    iteration = 0
    total_scraped = len(scraped_elements)
    total_recipes_collected = 0
    
    current_batch = pending[:] if pending else all_elements[:1000]
    
    while current_batch:
        iteration += 1
        print(f"\n{'='*40}")
        print(f"📦 Batch {iteration} - {len(current_batch)} elements")
        print(f"{'='*40}")
        
        batch_results = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(scrape_element_page, elem): elem 
                      for elem in current_batch[:BATCH_SIZE]}
            
            for future in as_completed(futures):
                result = future.result()
                if result:
                    batch_results.append(result)
                    total_scraped += 1
                    total_recipes_collected += result['total_recipes']
        
        # Save batch to MongoDB
        if batch_results:
            new_names = save_batch_to_mongodb(batch_results)
            
            # Discover new elements from this batch
            discovered = discover_new_elements(batch_results, scraped_elements)
            
            # Update progress
            scraped_elements.update([d['element']['name'] for d in batch_results])
            
            print(f"\n   📊 Progress: {total_scraped} elements | {total_recipes_collected} recipes")
            if discovered:
                print(f"   🔍 Discovered {len(discovered)} new elements to scrape")
            
            # Save stats
            update_scraping_stats({
                "scraped_elements": list(scraped_elements),
                "total_recipes": total_recipes_collected,
                "total_elements": total_scraped,
                "last_batch": iteration,
                "last_updated": time.time()
            })
        
        # Get next batch
        all_current = list(elements_coll.find({}, {"name": 1}))
        all_names = set(e["name"] for e in all_current)
        
        # Add newly discovered
        newly_discovered = [n for n in all_names if n not in scraped_elements]
        
        # Check for more from recipes
        recipe_elem_names = set(extract_elements_from_recipes())
        more_new = [n for n in recipe_elem_names if n not in scraped_elements]
        
        current_batch = list(set(newly_discovered + more_new))[:BATCH_SIZE]
        
        # If batch is empty but we haven't covered all, check recipes
        if not current_batch:
            recipe_elems = extract_elements_from_recipes()
            current_batch = [e for e in recipe_elems if e not in scraped_elements][:BATCH_SIZE]
        
        if not current_batch:
            print("\n✅ No more elements to scrape!")
        
        # Delay between batches
        print(f"   ⏳ Waiting {BATCH_DELAY}s before next batch...")
        time.sleep(BATCH_DELAY)
    
    # Final summary
    print("\n" + "=" * 60)
    print("🎉 SCRAPING COMPLETE!")
    print("=" * 60)
    print(f"\n📊 Final Statistics:")
    print(f"   Elements in DB: {elements_coll.count_documents({})}")
    print(f"   Recipes in DB: {recipes_coll.count_documents({})}")
    print(f"   Elements scraped: {total_scraped}")
    print(f"   Recipes collected: {total_recipes_collected}")
    
    # Sample queries to verify
    print("\n🔍 Sample data from MongoDB:")
    sample = list(recipes_coll.aggregate([{"$sample": {"size": 5}}]))
    for s in sample:
        print(f"   {s.get('first_emoji','')} {s.get('first','')} + {s.get('second_emoji','')} {s.get('second','')} = {s.get('result_emoji','')} {s.get('result','')}")
    
    print("\n✅ Data ready for your Discord bot!")

if __name__ == "__main__":
    main()
