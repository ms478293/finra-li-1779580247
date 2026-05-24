#!/usr/bin/env python3
"""Merge GH artifact CSVs into linkedin_cache.db. Output: CRD set already-found."""
import csv
import sqlite3
import sys
from pathlib import Path

LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"


def main():
    artifacts_dir = Path(sys.argv[1])
    conn = sqlite3.connect(LI_DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS linkedin (
        crd TEXT PRIMARY KEY,
        url TEXT,
        query TEXT,
        engine TEXT,
        pass INTEGER,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.commit()

    total = 0
    hits = 0
    for csv_file in artifacts_dir.glob("*/out_*.csv"):
        with csv_file.open() as f:
            for r in csv.DictReader(f):
                crd = r.get("crd")
                url = r.get("linkedin_url") or ""
                if not crd:
                    continue
                if url.startswith("http"):
                    conn.execute(
                        "INSERT OR REPLACE INTO linkedin(crd,url,query,engine,pass) VALUES(?,?,?,?,?)",
                        (crd, url, r.get("query", ""), "ddgs", 1))
                    hits += 1
                else:
                    # mark as attempted but not found (skip on next pass NO — re-try later)
                    pass
                total += 1
        conn.commit()
    print(f"merged: {total} rows, {hits} LinkedIn hits", file=sys.stderr)


if __name__ == "__main__":
    main()
