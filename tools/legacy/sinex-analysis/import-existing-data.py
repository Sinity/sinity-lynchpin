#!/usr/bin/env python3
"""Import existing analysis data into SQLite database"""

import json
import sqlite3
import csv
from datetime import datetime

print("📥 Importing existing data into SQLite...")

# Connect to database
conn = sqlite3.connect('metrics.db')
c = conn.cursor()

# Create tables if they don't exist
c.execute('''
CREATE TABLE IF NOT EXISTS metrics (
    commit_hash TEXT,
    timestamp INTEGER,
    metric_type TEXT,
    metric_value REAL,
    metric_json TEXT,
    tags TEXT,
    PRIMARY KEY (commit_hash, metric_type)
)
''')

c.execute('''
CREATE TABLE IF NOT EXISTS commits (
    hash TEXT PRIMARY KEY,
    timestamp INTEGER,
    author TEXT,
    message TEXT,
    branch TEXT,
    parent_hash TEXT
)
''')

# Import language evolution data
with open('data/language-evolution.json', 'r') as f:
    data = json.load(f)
    
    for i, entry in enumerate(data):
        date_str = entry['date']
        # Parse date (format: "2025-05-30")
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        timestamp = int(dt.timestamp())
        
        # Use index as pseudo commit hash for now
        commit_hash = f"snapshot_{i:03d}_{date_str}"
        
        # Store total LOC
        total_loc = entry['languages']['Total']
        c.execute('''
            INSERT OR REPLACE INTO metrics (commit_hash, timestamp, metric_type, metric_value, metric_json)
            VALUES (?, ?, ?, ?, ?)
        ''', (commit_hash, timestamp, 'loc_total', total_loc, json.dumps(entry['languages'])))
        
        # Store per-language metrics
        for lang, loc in entry['languages'].items():
            if lang != 'Total':
                c.execute('''
                    INSERT OR REPLACE INTO metrics (commit_hash, timestamp, metric_type, metric_value)
                    VALUES (?, ?, ?, ?)
                ''', (commit_hash, timestamp, f'loc_{lang.lower()}', loc))
        
        print(f"  ✓ {date_str}: {total_loc:,} lines")

# Import commit heatmap data
print("\n📊 Importing commit activity data...")
with open('data/commit-heatmap.csv', 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        date_str = row['date']
        count = int(row['count'])
        
        # Store daily commit count
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        timestamp = int(dt.timestamp())
        
        c.execute('''
            INSERT OR REPLACE INTO metrics (commit_hash, timestamp, metric_type, metric_value)
            VALUES (?, ?, ?, ?)
        ''', (f"daily_{date_str}", timestamp, 'commits_per_day', count))

conn.commit()

# Show summary
total_metrics = c.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
print(f"\n✅ Imported {total_metrics} metrics into database")

# Sample queries
print("\n📊 Sample data:")
print("\nLatest LOC metrics:")
for row in c.execute("""
    SELECT datetime(timestamp, 'unixepoch'), metric_value 
    FROM metrics 
    WHERE metric_type = 'loc_total' 
    ORDER BY timestamp DESC 
    LIMIT 5
"""):
    print(f"  {row[0]}: {row[1]:,.0f} lines")

print("\nLanguage breakdown (latest):")
latest = c.execute("""
    SELECT metric_json 
    FROM metrics 
    WHERE metric_type = 'loc_total' 
    ORDER BY timestamp DESC 
    LIMIT 1
""").fetchone()

if latest:
    langs = json.loads(latest[0])
    for lang, loc in sorted(langs.items(), key=lambda x: x[1], reverse=True)[:5]:
        if lang != 'Total':
            print(f"  {lang}: {loc:,} lines")

conn.close()
print("\n✨ Done! Database ready for analysis.")