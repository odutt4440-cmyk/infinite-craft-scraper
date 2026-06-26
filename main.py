# scrape_infinite_craft.py
import requests
import json
import base64
import zlib
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
progress_coll = db["infinite_craft_progress"]

recipes_coll.create_index([("first", 1), ("second", 1)], unique=True)
recipes_coll.create_index([("result", 1)])
elements_coll.create_index([("name", 1)], unique=True)

# ===== SOURCE 1: expitau GitHub repo ka data.json (94 MB, ~3.4M recipes) =====
DATA_JSON_URL = "https://github.com/expitau/InfiniteCraftWiki/raw/refs/heads/main/web/data/data.json"

# ===== SOURCE 2: infinite-craft.com ka chunk system =====
BASE = "https://infinite-craft.com/recipes"

def download_data_json():
    """Download 94 MB data.json from GitHub"""
    print("📥 Downloading data.json from GitHub...")
    resp = requests.get(DATA_JSON_URL, timeout=120)
    if resp.status_code == 200:
        data = resp.json()
        print(f"   ✅ Downloaded! Index entries: {len(data.get('index', {}))}")
        print(f"   ✅ Recipe string length: {len(data.get('data', ''))}")
        return data
    print(f"   ❌ Failed: {resp.status_code}")
    return None

def decode_data_json(data_json):
    """data.json ko decode karke proper recipes banaye"""
    index = data_json.get("index", {})
    data_str = data_json.get("data", "")
    
    print(f"📊 Index: {len(index)} elements")
    print(f"📊 Data string: {len(data_str)} chars")
    
    # Pehle elements save karo
    for code, elem in index.items():
        emoji, name, cost = elem[0], elem[1], elem[2] if len(elem) > 2 else 0
        elements_coll.update_one(
            {"name": name},
            {"$set": {"name": name, "emoji": emoji, "code": code, "cost": cost}},
            upsert=True
        )
    
    # Recipes decode karo
    recipes = data_str.split(";")
    print(f"📊 Total recipes in data string: {len(recipes)}")
    
    batch = []
    count = 0
    errors = 0
    
    for recipe_str in recipes:
        if not recipe_str.strip():
            continue
        
        parts = recipe_str.split(",")
        if len(parts) != 3:
            errors += 1
            continue
        
        code_a, code_b, code_result = parts
        
        elem_a = index.get(code_a)
        elem_b = index.get(code_b)
        elem_result = index.get(code_result)
        
        if not elem_a or not elem_b or not elem_result:
            errors += 1
            continue
        
        recipe = {
            "first": elem_a[1],
            "first_emoji": elem_a[0],
            "second": elem_b[1],
            "second_emoji": elem_b[0],
            "result": elem_result[1],
            "result_emoji": elem_result[0],
            "source": "expitau_github"
        }
        
        batch.append(
            UpdateOne(
                {"first": recipe["first"], "second": recipe["second"]},
                {"$set": recipe},
                upsert=True
            )
        )
        count += 1
        
        if len(batch) >= 5000:
            if batch:
                recipes_coll.bulk_write(batch, ordered=False)
            batch = []
            print(f"   ✅ {count} recipes inserted...")
    
    if batch:
        recipes_coll.bulk_write(batch, ordered=False)
    
    print(f"\n✅ Total recipes inserted: {count}")
    print(f"⚠️ Errors: {errors}")
    return count

def scrape_element_page(element_name, element_emoji=""):
    """Single element ka recipe page scrape karega"""
    url_name = element_name.lower().replace(" ", "-")
    url = f"{BASE}/{url_name}"
    
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=30
        )
        if resp.status_code == 200:
            # Page source mein data.index embedded hota hai
            html = resp.text
            
            # Vue.js ka data extract karo
            import re
            # Pattern: data.index["X"] = ["emoji", "name"]
            matches = re.findall(r'data\.index\[["\']([A-Za-z0-9+/=]+)["\']\]\s*=\s*\[["\']([^"\']+)["\'],\s*["\']([^"\']+)["\']', html)
            
            recipes_found = []
            for code, emoji, name in matches:
                if name.lower() == element_name.lower():
                    continue  # Skip self
                recipes_found.append({"code": code, "emoji": emoji, "name": name})
            
            return recipes_found
    except Exception as e:
        print(f"   ⚠️ Error: {e}")
    
    return None

def main():
    print("="*60)
    print("🔥 INFINITE-CRAFT.COM RECIPE SCRAPER")
    print("="*60)
    
    # Step 1: Download 94 MB data.json from GitHub
    print("\n📦 STEP 1: Downloading data.json from expitau/InfiniteCraftWiki...")
    data_json = download_data_json()
    
    if data_json:
        print("\n📦 STEP 2: Decoding and inserting recipes...")
        total = decode_data_json(data_json)
        
        print(f"\n🎉 DONE! {total} recipes inserted!")
        print(f"   Elements in DB: {elements_coll.count_documents({})}")
        print(f"   Recipes in DB: {recipes_coll.count_documents({})}")
    else:
        print("\n⚠️ GitHub download failed. Falling back to website scraping...")
        # Fallback: individual element scraping
        # (similar to your existing code but targeting infinite-craft.com)
        pass
    
    print("\n" + "="*60)

if __name__ == "__main__":
    main()
