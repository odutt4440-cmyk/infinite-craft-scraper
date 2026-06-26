# final_scraper.py
import requests
import re
import json
from pymongo import MongoClient, UpdateOne
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import os
import time
from datetime import datetime

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = "infinite_craft_bot"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db["website_recipes"]
elements_coll = db["website_elements"]
progress_coll = db["scraping_progress"]

recipes_coll.create_index([("first", 1), ("second", 1)])
recipes_coll.create_index([("result", 1)])
elements_coll.create_index([("name", 1)], unique=True)
elements_coll.create_index([("code", 1)], unique=True)

BASE = "https://infinite-craft.com/recipes"
BASE64_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-='

def from_base64(value):
    """Base64 string se number decode karo"""
    result = 0
    for char in value:
        result = (result * 64 + BASE64_CHARS.index(char))
    return result

def get_chunk(n):
    """Element number ka chunk number nikaalo"""
    return int(n // 1000)

# ===== STEP 1: Pehle metadata se total elements count lo =====
def get_metadata():
    """metadata.json se recipe count lo"""
    resp = requests.get(f"{BASE}/data/metadata.json", timeout=30)
    if resp.status_code == 200:
        return resp.json()
    return {"recipeCount": 3470353}

# ===== STEP 2: Index chunks download karo (yehi elements + recipes deta hai) =====
def download_all_chunks():
    """Saare index chunks download karo jo elements aur recipes dono contain karte hain"""
    all_elements = {}  # code -> {name, emoji, cost}
    all_recipes = []   # list of (first_code, second_code, result_code)
    
    # Metadata se index count pata karo
    metadata = get_metadata()
    print(f"📊 Total recipes: {metadata.get('recipeCount', '?')}")
    
    # Index chunks download karo - website ka pattern
    # Actually, website ka data GitHub repo jaisa hi hai
    # Toh direct GitHub se data.json lo
    print("\n📥 Downloading data from GitHub (same data as website)...")
    resp = requests.get(
        "https://github.com/expitau/InfiniteCraftWiki/raw/refs/heads/main/web/data/data.json",
        timeout=300
    )
    
    if resp.status_code != 200:
        print("❌ GitHub download failed!")
        return None, None
    
    data = resp.json()
    index = data.get("index", {})
    data_str = data.get("data", "")
    
    print(f"   ✅ Elements: {len(index)}")
    print(f"   ✅ Recipes: {len(data_str.split(';'))}")
    
    return index, data_str

# ===== STEP 3: Elements ko unke code se scrape karo =====
def scrape_element_page(code, name, emoji):
    """Single element ka recipe page scrape karo"""
    url = f"{BASE}/?e={code}"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30
        )
        if resp.status_code == 200:
            html = resp.text
            
            # Extract recipes from HTML
            # Pattern: <tr><td>emoji name</td><td>+</td><td>emoji name</td><td>=</td><td>emoji name</td></tr>
            recipes = []
            # Find all table rows with recipe pattern
            pattern = r'<td[^>]*>([^<]+)</td>\s*<td[^>]*>\s*\+\s*</td>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>\s*=\s*</td>\s*<td[^>]*>([^<]+)</td>'
            matches = re.findall(pattern, html)
            
            for m in matches:
                first_raw = m[0].strip()
                second_raw = m[1].strip()
                result_raw = m[2].strip()
                
                # Emoji aur name alag karo
                f_emoji_match = re.match(r'([\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]+)\s*(.*)', first_raw)
                s_emoji_match = re.match(r'([\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]+)\s*(.*)', second_raw)
                r_emoji_match = re.match(r'([\U0001F300-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u00A9\u00AE\u2122\u200D\uFE0F\u20E3\u20E0\u0023\u002A\u0030-\u0039\uFE0F]+)\s*(.*)', result_raw)
                
                f_emoji = f_emoji_match.group(1) if f_emoji_match else ""
                f_name = f_emoji_match.group(2).strip() if f_emoji_match else first_raw
                s_emoji = s_emoji_match.group(1) if s_emoji_match else ""
                s_name = s_emoji_match.group(2).strip() if s_emoji_match else second_raw
                r_emoji = r_emoji_match.group(1) if r_emoji_match else ""
                r_name = r_emoji_match.group(2).strip() if r_emoji_match else result_raw
                
                if f_name and s_name and r_name:
                    recipes.append({
                        "first": f_name,
                        "first_emoji": f_emoji,
                        "second": s_name,
                        "second_emoji": s_emoji,
                        "result": r_name,
                        "result_emoji": r_emoji,
                        "result_code": code
                    })
            
            return {"code": code, "name": name, "emoji": emoji, "recipes": recipes}
    except:
        pass
    return None

# ===== MAIN: Teeno steps ek saath =====
def main():
    print("="*60)
    print("🔥 INFINITE-CRAFT.COM COMPLETE SCRAPER")
    print("="*60)
    
    start_time = time.time()
    
    # STEP 1: Index aur recipes download karo
    print("\n📦 PHASE 1: Downloading data from source...")
    index, data_str = download_all_chunks()
    
    if not index:
        print("❌ Failed to get data!")
        return
    
    # STEP 2: Elements + Recipes simultaneously insert karo
    print("\n📦 PHASE 2: Inserting elements and recipes...")
    
    # Pehle saare elements DB mein daalo
    elem_batch = []
    for code, elem_data in index.items():
        emoji = elem_data[0] if len(elem_data) > 0 else ""
        name = elem_data[1] if len(elem_data) > 1 else ""
        cost = elem_data[2] if len(elem_data) > 2 else 0
        
        elem_batch.append({
            "code": code,
            "name": name,
            "emoji": emoji,
            "cost": cost
        })
        
        if len(elem_batch) >= 5000:
            for e in elem_batch:
                elements_coll.update_one(
                    {"code": e["code"]},
                    {"$set": e},
                    upsert=True
                )
            print(f"   ✅ {len(elem_batch)} elements inserted...")
            elem_batch = []
    
    if elem_batch:
        for e in elem_batch:
            elements_coll.update_one(
                {"code": e["code"]},
                {"$set": e},
                upsert=True
            )
    
    print(f"   ✅ All {len(index)} elements inserted!")
    
    # Ab recipes insert karo
    recipes = data_str.split(";")
    recipe_batch = []
    recipe_count = 0
    error_count = 0
    
    print(f"\n📦 PHASE 3: Inserting {len(recipes)} recipes...")
    
    for recipe_str in recipes:
        if not recipe_str.strip():
            continue
        
        parts = recipe_str.split(",")
        if len(parts) != 3:
            error_count += 1
            continue
        
        code_a, code_b, code_result = parts
        
        elem_a = index.get(code_a)
        elem_b = index.get(code_b)
        elem_result = index.get(code_result)
        
        if not elem_a or not elem_b or not elem_result:
            error_count += 1
            continue
        
        recipe = {
            "first": elem_a[1],
            "first_emoji": elem_a[0],
            "second": elem_b[1],
            "second_emoji": elem_b[0],
            "result": elem_result[1],
            "result_emoji": elem_result[0]
        }
        
        recipe_batch.append(
            UpdateOne(
                {"first": recipe["first"], "second": recipe["second"]},
                {"$set": recipe},
                upsert=True
            )
        )
        recipe_count += 1
        
        if len(recipe_batch) >= 10000:
            if recipe_batch:
                recipes_coll.bulk_write(recipe_batch, ordered=False)
            recipe_batch = []
            elapsed = time.time() - start_time
            rate = recipe_count / (elapsed / 60)
            print(f"   ✅ {recipe_count} recipes inserted... ({rate:.0f}/min)")
    
    if recipe_batch:
        recipes_coll.bulk_write(recipe_batch, ordered=False)
    
    elapsed = time.time() - start_time
    print(f"\n✅ COMPLETE! {recipe_count} recipes inserted in {elapsed/60:.1f} minutes")
    print(f"   Errors: {error_count}")
    print(f"   Total elements in DB: {elements_coll.count_documents({})}")
    print(f"   Total recipes in DB: {recipes_coll.count_documents({})}")
    
    # STEP 4: Website se bache hue elements ki recipes scrape karo
    # (Agar koi element missing ho)
    print("\n📦 PHASE 4: Checking for missing elements on website...")
    
    try:
        resp = requests.get(f"{BASE}/data/metadata.json", timeout=30)
        if resp.status_code == 200:
            meta = resp.json()
            print(f"   Website total recipes: {meta.get('recipeCount', '?')}")
            print(f"   Our DB recipes: {recipe_count}")
    except:
        pass
    
    print("\n" + "="*60)
    print("🎉 DONE! Ready to use!")
    print("="*60)

if __name__ == "__main__":
    main()
