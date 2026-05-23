#!/usr/bin/env python3
"""Extract broker list from FINRA cache.db -> N JSON chunks for GH matrix."""
import json
import sqlite3
import sys
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
N_CHUNKS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/finra_li_matrix/chunks")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    conn = sqlite3.connect(FINRA_DB)
    rows = []
    for crd, data_str in conn.execute("SELECT crd, data FROM detail WHERE data IS NOT NULL"):
        try:
            outer = json.loads(data_str)
            src = outer["hits"]["hits"][0]["_source"]
            content = src["content"]
            if isinstance(content, str):
                content = json.loads(content)
        except Exception:
            continue
        bi = content.get("basicInformation") or {}
        cur_emp = (content.get("currentEmployments") or [{}])
        cur0 = cur_emp[0] if cur_emp else {}
        first = (bi.get("firstName") or "").strip()
        last = (bi.get("lastName") or "").strip()
        if not first or not last:
            continue
        rows.append({
            "crd": str(crd),
            "first": first,
            "last": last,
            "full": f"{first} {last}",
            "aliases": bi.get("otherNames") or [],
            "firm": (cur0.get("firmName") or "").strip(),
            "city": (cur0.get("branchCity") or "").strip(),
            "state": (cur0.get("branchState") or "").strip(),
        })
    print(f"total brokers: {len(rows)}")
    rows.sort(key=lambda r: r["crd"])
    chunk_size = (len(rows) + N_CHUNKS - 1) // N_CHUNKS
    for i in range(N_CHUNKS):
        chunk = rows[i * chunk_size:(i + 1) * chunk_size]
        out = OUT_DIR / f"chunk_{i:02d}.json"
        out.write_text(json.dumps(chunk))
        print(f"  {out.name}: {len(chunk)} brokers ({out.stat().st_size/1024/1024:.1f}MB)")


if __name__ == "__main__":
    main()
