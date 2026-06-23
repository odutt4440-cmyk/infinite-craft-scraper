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
import xml.etree.ElementTree as ET
from dotenv import load_dotenv

load_dotenv()

# ===== CONFIG =====
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = os.getenv("DB_NAME", "infinite_craft")
RECIPES_COLL = os.getenv("RECIPES_COLL", "website_recipes")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "10"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db[RECIPES_COLL]
elements_coll = db["website_elements"]
progress_coll = db["scraping_progress"]

recipes_coll.create_index([("result", 1)])
recipes_coll.create_index([("first", 1), ("second", 1)])
elements_coll.create_index([("name", 1)], unique=True)

ua = UserAgent()
BASE = "https://infinitecraftrecipe.com"

# Railway timeout handle - restart ke baad continue
START_TIME = time.time()
TIMEOUT = 2400  # 40 min (Railway 30 min timeout + buffer)

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
            time.sleep(0.5 + random.uniform(0, 0.5))
            r = requests.get(url, headers=get_headers(), timeout=30)
            if r.status_code == 200: return r
            if r.status_code == 429: time.sleep((a+1)*10)
            if r.status_code == 404: return None
        except:
            if a < retries-1: time.sleep(5)
    return None

def extract_emoji_prefix(text):
    if not text: return "", ""
    text = text.strip()
    emoji_match = re.match(r'^([\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]+\s*)(.*)', text)
    if emoji_match:
        return emoji_match.group(1).strip(), emoji_match.group(2).strip()
    return "", text

def scrape_recipes_from_page(soup):
    recipes = []
    seen = set()
    body = soup.find('body')
    if not body: return recipes
    body_text = body.get_text(separator='\n')
    lines = body_text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line or len(line) > 300: continue
        m = re.match(
            r'((?:[\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039]+\s*)?'
            r'([A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F0-9\s\'\-\.\(\)]+?))\s*\+\s*'
            r'((?:[\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039]+\s*)?'
            r'([A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F0-9\s\'\-\.\(\)]+?))\s*=\s*'
            r'((?:[\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039]+\s*)?'
            r'([A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F0-9\s\'\-\.\(\)]+?))(?:\s|$)',
            line
        )
        if m:
            raw1, f_name, raw2, s_name, raw3, r_name = m.groups()
            f_name = f_name.strip()
            s_name = s_name.strip()
            r_name = r_name.strip()
            if f_name and s_name and r_name:
                key = (f_name.lower(), s_name.lower(), r_name.lower())
                if key not in seen:
                    seen.add(key)
                    f_emoji, _ = extract_emoji_prefix(raw1)
                    s_emoji, _ = extract_emoji_prefix(raw2)
                    r_emoji, _ = extract_emoji_prefix(raw3)
                    recipes.append({
                        "first": f_name, "first_emoji": f_emoji,
                        "second": s_name, "second_emoji": s_emoji,
                        "result": r_name, "result_emoji": r_emoji
                    })
    return recipes

def scrape_element(name):
    if not name or not name.strip(): return None
    name = name.strip()
    url_name = quote(name.replace(' ', '-'))
    url = f"{BASE}/recipes/{url_name}"
    print(f"  🔍 {name[:30]}", end="")
    resp = fetch(url)
    if not resp:
        print(" ❌")
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
    print(f" ✅ {len(recipes)} recipes")
    return {
        "element": {"name": name, "emoji": elem_emoji, "url": url},
        "recipes": recipes
    }

def get_deck_elements():
    all_elems = {"Water": "💧", "Fire": "🔥", "Wind": "💨", "Earth": "🌎"}
    print("\n📥 Phase 1: Decks se elements...")
    for page in range(1, 7):
        url = f"{BASE}/decks" if page == 1 else f"{BASE}/decks?page={page}"
        resp = fetch(url)
        if not resp: break
        soup = BeautifulSoup(resp.text, 'lxml')
        deck_urls = []
        for link in soup.find_all('a', href=True):
            if '/deck/' in link['href']:
                deck_urls.append(link['href'] if link['href'].startswith('http') else f"{BASE}{link['href']}")
        print(f"   Page {page}: {len(deck_urls)} decks")
        for d in deck_urls:
            resp2 = fetch(d)
            if not resp2: continue
            soup2 = BeautifulSoup(resp2.text, 'lxml')
            for l2 in soup2.find_all('a', href=True):
                if '/recipes/' in l2['href'] and 'login' not in l2['href'].lower():
                    txt = l2.get_text(strip=True)
                    em, nm = extract_emoji_prefix(txt)
                    if not nm:
                        nm = unquote(l2['href'].split('/recipes/')[-1]).replace('-', ' ')
                    if nm and nm not in all_elems:
                        all_elems[nm] = em
    print(f"   ✅ {len(all_elems)} elements from decks")
    return all_elems

def get_sitemap_elements():
    all_elems = {}
    print("\n📥 Phase 2: Sitemaps se elements...")
    robots = fetch(f"{BASE}/robots.txt")
    if not robots: return all_elems
    sitemaps = re.findall(r'Sitemap:\s*(https?://\S+)', robots.text)
    print(f"   {len(sitemaps)} sitemaps")
    for sm in sitemaps:
        print(f"   📄 {sm.split('/')[-1]}", end="")
        resp = fetch(sm)
        if not resp:
            print(" ❌")
            continue
        try:
            root = ET.fromstring(resp.content)
            ns = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
            count = 0
            for loc in root.findall('.//ns:loc', ns):
                if loc.text and '/recipes/' in loc.text:
                    name_part = loc.text.split('/recipes/')[-1].split('?')[0]
                    name = unquote(name_part).replace('-', ' ')
                    if name and name not in all_elems:
                        all_elems[name] = ""
                        count += 1
            print(f" ✅ {count}")
        except Exception as e:
            print(f" ⚠️ {e}")
    print(f"   ✅ {len(all_elems)} from sitemaps")
    return all_elems

def save(data):
    if not data: return set()
    new = set()
    if data.get('element'):
        try:
            elements_coll.update_one({"name": data['element']['name']}, {"$set": data['element']}, upsert=True)
        except: pass
    for r in data.get('recipes', []):
        new.update([r['first'], r['second'], r['result']])
        try:
            recipes_coll.update_one(
                {"first": r['first'], "second": r['second']},
                {"$set": r},
                upsert=True
            )
        except: pass
    return new

def load_progress():
    d = progress_coll.find_one({"_id": "p"})
    return set(d.get("e", [])) if d else set()

def save_progress(s):
    progress_coll.update_one({"_id": "p"}, {"$set": {"e": list(s)}}, upsert=True)

def main():
    print("=" * 60)
    print("🔥 INFINITE CRAFT - ALL DATA (18L+ RECIPES)")
    print("=" * 60)
    
    scraped = load_progress()
    
    print(f"\n📊 DB Status:")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Already scraped: {len(scraped)}")
    
    # Phase 1: Decks
    elements = get_deck_elements()
    
    # Phase 2: Sitemaps
    sitemap_elems = get_sitemap_elements()
    for nm in sitemap_elems:
        if nm not in elements:
            elements[nm] = ""
    
    print(f"\n📊 Total elements: {len(elements)}")
    
    # Save elements
    for nm, em in elements.items():
        try:
            elements_coll.update_one({"name": nm}, {"$set": {"name": nm, "emoji": em}}, upsert=True)
        except: pass
    
    # Phase 3: Scrape recipes
    elem_list = list(elements.keys())
    pending = [e for e in elem_list if e not in scraped]
    
    print(f"\n📥 Phase 3: Recipe scrape ({len(pending)} pending)")
    
    batch_num = 0
    while pending:
        # Railway timeout check
        if time.time() - START_TIME > TIMEOUT:
            print(f"\n⏰ Timeout approaching ({TIMEOUT}s). Saving progress and exiting...")
            save_progress(scraped)
            print("✅ Progress saved. Railway will restart and continue.")
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
                            if n not in elements:
                                elements[n] = ""
                                elem_list.append(n)
                except: pass
        
        save_progress(scraped)
        
        te = elements_coll.count_documents({})
        tr = recipes_coll.count_documents({})
        pending = [e for e in elem_list if e not in scraped]
        
        print(f"   📊 {len(scraped)}/{len(elements)} done | {tr} recipes | {te} elements | {len(pending)} pending")
    
    print("\n" + "=" * 60)
    print("🎉 ALL DATA COLLECTED!")
    print("=" * 60)
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")

if __name__ == "__main__":
    main()
