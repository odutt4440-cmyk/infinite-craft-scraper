import requests
import sqlite3
import json
import time
import gzip
import shutil
import os

DATA_URL = "https://github.com/expitau/InfiniteCraftWiki/raw/refs/heads/main/web/data/data.json"
DB_PATH = "infinite_craft.db"
GZ_PATH = "infinite_craft.db.gz"

def setup_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS elements (
            code TEXT PRIMARY KEY,
            name TEXT UNIQUE,
            emoji TEXT,
            cost INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            first TEXT,
            second TEXT,
            result TEXT,
            first_emoji TEXT,
            second_emoji TEXT,
            result_emoji TEXT,
            UNIQUE(first, second)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_recipes_result ON recipes(result)")
    conn.commit()
    return conn

def download_data():
    print("📥 Downloading data.json...")
    resp = requests.get(DATA_URL, timeout=600)
    data = resp.json()
    return data["index"], data["data"]

def insert_elements(conn, index):
    c = conn.cursor()
    count = 0
    c.execute("BEGIN TRANSACTION")
    for code, elem in index.items():
        c.execute(
            "INSERT OR IGNORE INTO elements VALUES (?, ?, ?, ?)",
            (code, elem[1], elem[0], elem[2] if len(elem) > 2 else 0)
        )
        count += 1
        if count % 50000 == 0:
            conn.commit()
            c.execute("BEGIN TRANSACTION")
            print(f"   ✅ {count} elements...")
    conn.commit()
    c.execute("SELECT COUNT(*) FROM elements")
    print(f"   ✅ Total: {c.fetchone()[0]} elements")

def insert_recipes(conn, index, data_str):
    c = conn.cursor()
    recipes = data_str.split(";")
    total = len(recipes)
    count = 0
    errors = 0
    start = time.time()
    
    c.execute("BEGIN TRANSACTION")
    for i, recipe_str in enumerate(recipes):
        if not recipe_str.strip():
            continue
        parts = recipe_str.split(",")
        if len(parts) != 3:
            errors += 1
            continue
        
        a = index.get(parts[0])
        b = index.get(parts[1])
        r = index.get(parts[2])
        if not a or not b or not r:
            errors += 1
            continue
        
        c.execute(
            "INSERT OR IGNORE INTO recipes VALUES (?, ?, ?, ?, ?, ?)",
            (a[1], b[1], r[1], a[0], b[0], r[0])
        )
        count += 1
        
        if count % 50000 == 0:
            conn.commit()
            c.execute("BEGIN TRANSACTION")
            elapsed = time.time() - start
            rate = count / (elapsed / 60)
            pct = (i / total) * 100
            eta = (total - i) / rate * 60 if rate > 0 else 0
            print(f"   ✅ {count} recipes ({pct:.1f}%) | {rate:.0f}/min | ETA: {eta:.0f}s")
    
    conn.commit()
    elapsed = time.time() - start
    c.execute("SELECT COUNT(*) FROM recipes")
    print(f"\n   ✅ DONE! {c.fetchone()[0]} recipes in {elapsed:.0f}s")

def compress_db():
    print("\n🗜️ Compressing database...")
    with open(DB_PATH, 'rb') as f_in:
        with gzip.open(GZ_PATH, 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    orig = os.path.getsize(DB_PATH) / (1024*1024)
    comp = os.path.getsize(GZ_PATH) / (1024*1024)
    print(f"   Original: {orig:.0f} MB → Compressed: {comp:.0f} MB")

if __name__ == "__main__":
    print("🔥 Building Infinite Craft Database...")
    conn = setup_db()
    index, data_str = download_data()
    insert_elements(conn, index)
    insert_recipes(conn, index, data_str)
    conn.close()
    compress_db()
    print(f"\n🎉 Done! Files: {os.path.abspath(DB_PATH)}, {os.path.abspath(GZ_PATH)}")
