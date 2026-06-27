# final_clean_scraper.py
import requests
import json
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv
import os
import time

load_dotenv()

# ===== NEW MONGO CREDENTIALS =====
MONGO_URI = "mongodb+srv://srishtidutt12_db_user:pCDhpXnv2bxhMs0r@cluster0.hcbkwxy.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "infinite_craft_bot"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db["website_recipes"]
elements_coll = db["website_elements"]

DATA_URL = "https://github.com/expitau/InfiniteCraftWiki/raw/refs/heads/main/web/data/data.json"

def setup_db():
    """Pehle check karo ki collections exist karte hain ya nahi, agar nahi toh create karo"""
    print("🔧 Setting up database...")
    
    # Check if collections already exist
    existing_collections = db.list_collection_names()
    
    if "website_elements" not in existing_collections:
        # Create collection with indexes
        elements_coll.create_index([("name", 1)], unique=True)
        elements_coll.create_index([("code", 1)], unique=True)
        print("   ✅ Elements collection created with indexes")
    else:
        print("   ✅ Elements collection already exists")
    
    if "website_recipes" not in existing_collections:
        recipes_coll.create_index([("first", 1), ("second", 1)])
        recipes_coll.create_index([("result", 1)])
        print("   ✅ Recipes collection created with indexes")
    else:
        print("   ✅ Recipes collection already exists")

def download_data():
    """Download data.json"""
    print("📥 Downloading data.json...")
    resp = requests.get(DATA_URL, timeout=300)
    if resp.status_code != 200:
        print(f"❌ Failed: {resp.status_code}")
        return None, None
    
    data = resp.json()
    index = data.get("index", {})
    data_str = data.get("data", "")
    
    print(f"   ✅ {len(index)} elements")
    print(f"   ✅ {len(data_str.split(';'))} recipes")
    return index, data_str

def insert_elements(index):
    """Bulk insert elements - NO duplicates"""
    print("\n📦 Inserting elements...")
    
    batch = []
    count = 0
    
    for code, elem in index.items():
        emoji = elem[0]
        name = elem[1]
        cost = elem[2] if len(elem) > 2 else 0
        
        batch.append(
            UpdateOne(
                {"name": name},
                {"$set": {
                    "code": code,
                    "name": name,
                    "emoji": emoji,
                    "cost": cost
                }},
                upsert=True
            )
        )
        count += 1
        
        if len(batch) >= 10000:
            try:
                elements_coll.bulk_write(batch, ordered=False)
            except:
                for op in batch:
                    try:
                        elements_coll.bulk_write([op], ordered=False)
                    except:
                        pass
            batch = []
            print(f"   ✅ {count} elements...")
    
    if batch:
        try:
            elements_coll.bulk_write(batch, ordered=False)
        except:
            for op in batch:
                try:
                    elements_coll.bulk_write([op], ordered=False)
                except:
                    pass
    
    final_count = elements_coll.count_documents({})
    print(f"   ✅ Total: {final_count} elements")

def insert_recipes(index, data_str):
    """Bulk insert recipes - FAST"""
    print("\n📦 Inserting recipes...")
    
    recipes = data_str.split(";")
    total = len(recipes)
    batch = []
    count = 0
    errors = 0
    start = time.time()
    
    for i, recipe_str in enumerate(recipes):
        if not recipe_str.strip():
            continue
        
        parts = recipe_str.split(",")
        if len(parts) != 3:
            errors += 1
            continue
        
        code_a, code_b, code_r = parts
        
        a = index.get(code_a)
        b = index.get(code_b)
        r = index.get(code_r)
        
        if not a or not b or not r:
            errors += 1
            continue
        
        batch.append(
            UpdateOne(
                {"first": a[1], "second": b[1]},
                {"$set": {
                    "first": a[1],
                    "first_emoji": a[0],
                    "second": b[1],
                    "second_emoji": b[0],
                    "result": r[1],
                    "result_emoji": r[0]
                }},
                upsert=True
            )
        )
        count += 1
        
        if len(batch) >= 10000:
            try:
                recipes_coll.bulk_write(batch, ordered=False)
            except:
                pass
            batch = []
            
            elapsed = time.time() - start
            rate = count / (elapsed / 60) if elapsed > 0 else 0
            pct = (i / total) * 100
            eta = (total - i) / rate * 60 if rate > 0 else 0
            
            print(f"   ✅ {count} recipes ({pct:.0f}%) | {rate:.0f}/min | ETA: {eta:.0f}s")
    
    if batch:
        try:
            recipes_coll.bulk_write(batch, ordered=False)
        except:
            pass
    
    elapsed = time.time() - start
    final_count = recipes_coll.count_documents({})
    print(f"\n   ✅ DONE! {final_count} recipes in {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"   ⚠️ Errors: {errors}")

def main():
    print("="*60)
    print("🔥 FINAL SCRAPER - NO DUPLICATES, NEW DB")
    print("="*60)
    
    overall_start = time.time()
    
    # Step 0: Setup database (NO DROP)
    setup_db()
    
    # Step 1: Download data
    index, data_str = download_data()
    if not index:
        return
    
    # Step 2: Insert elements
    insert_elements(index)
    
    # Step 3: Insert recipes
    insert_recipes(index, data_str)
    
    # Summary
    total_time = time.time() - overall_start
    elem_count = elements_coll.count_documents({})
    recipe_count = recipes_coll.count_documents({})
    
    print(f"\n{'='*60}")
    print(f"🎉 ALL DONE!")
    print(f"   Elements: {elem_count}")
    print(f"   Recipes: {recipe_count}")
    print(f"   Time: {total_time:.0f}s ({total_time/60:.1f} min)")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
