# final_scraper.py
import requests
import json
from pymongo import MongoClient, UpdateOne
from dotenv import load_dotenv
import os
import time

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://odutt4440_db_user:Gaming123@cluster0.hcbkwxy.mongodb.net/?appName=Cluster0")
DB_NAME = "infinite_craft_bot"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
recipes_coll = db["website_recipes"]
elements_coll = db["website_elements"]

# Pehle saare indexes drop karo jo conflict kar sakte hain
for index_name in ["first_1_second_1", "result_1"]:
    try:
        recipes_coll.drop_index(index_name)
    except:
        pass

# Ab naye indexes banao (unique nahi, simple indexes)
recipes_coll.create_index([("first", 1), ("second", 1)])
elements_coll.create_index([("name", 1)], unique=True)

DATA_URL = "https://github.com/expitau/InfiniteCraftWiki/raw/refs/heads/main/web/data/data.json"

def main():
    print("="*60)
    print("🔥 FINAL SCRAPER - NO INDEX CONFLICT")
    print("="*60)
    
    start = time.time()
    
    # Step 1: Download
    print("\n📥 Downloading data.json...")
    resp = requests.get(DATA_URL, timeout=300)
    if resp.status_code != 200:
        print(f"❌ Failed: {resp.status_code}")
        return
    
    data = resp.json()
    index = data.get("index", {})
    data_str = data.get("data", "")
    
    print(f"✅ Elements: {len(index)}")
    print(f"✅ Recipes in string: {len(data_str.split(';'))}")
    
    # Step 2: Elements insert karo
    print("\n📦 Inserting elements...")
    elem_ops = []
    for code, elem in index.items():
        elem_ops.append(
            UpdateOne(
                {"name": elem[1]},
                {"$set": {
                    "code": code,
                    "name": elem[1],
                    "emoji": elem[0],
                    "cost": elem[2] if len(elem) > 2 else 0
                }},
                upsert=True
            )
        )
        if len(elem_ops) >= 5000:
            elements_coll.bulk_write(elem_ops, ordered=False)
            elem_ops = []
    
    if elem_ops:
        elements_coll.bulk_write(elem_ops, ordered=False)
    print(f"✅ {len(index)} elements done!")
    
    # Step 3: Recipes insert karo
    print("\n📦 Inserting recipes...")
    recipes = data_str.split(";")
    batch = []
    count = 0
    
    for recipe_str in recipes:
        if not recipe_str.strip():
            continue
        
        parts = recipe_str.split(",")
        if len(parts) != 3:
            continue
        
        code_a, code_b, code_r = parts
        
        a = index.get(code_a)
        b = index.get(code_b) 
        r = index.get(code_r)
        
        if not a or not b or not r:
            continue
        
        batch.append(
            UpdateOne(
                {"first": a[1], "second": b[1]},
                {"$set": {
                    "first": a[1], "first_emoji": a[0],
                    "second": b[1], "second_emoji": b[0],
                    "result": r[1], "result_emoji": r[0]
                }},
                upsert=True
            )
        )
        count += 1
        
        if len(batch) >= 10000:
            recipes_coll.bulk_write(batch, ordered=False)
            batch = []
            elapsed = time.time() - start
            print(f"   ✅ {count} recipes... ({count/(elapsed/60):.0f}/min)")
    
    if batch:
        recipes_coll.bulk_write(batch, ordered=False)
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"🎉 COMPLETE!")
    print(f"   Elements: {elements_coll.count_documents({})}")
    print(f"   Recipes: {recipes_coll.count_documents({})}")
    print(f"   Time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
