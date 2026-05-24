#!/usr/bin/env python3
"""Extract ACTIVE brokers only (BC_Scope=Active), chunk for matrix."""
import json
import sqlite3
import sys
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"
N_CHUNKS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/finra_li_matrix/chunks")

def main():
    li = sqlite3.connect(LI_DB)
    found = {r[0] for r in li.execute("SELECT crd FROM linkedin WHERE url LIKE 'http%'")}
    print(f"already found: {len(found)}", file=sys.stderr)

    conn = sqlite3.connect(FINRA_DB)
    rows = []
    active_total = 0
    for crd, data_str in conn.execute("SELECT crd, data FROM detail WHERE data IS NOT NULL"):
        try:
            outer = json.loads(data_str)
            content = outer["hits"]["hits"][0]["_source"]["content"]
            if isinstance(content, str): content = json.loads(content)
        except Exception: continue
        bi = content.get("basicInformation") or {}
        if bi.get("bcScope") != "Active":
            continue
        active_total += 1
        if str(crd) in found: continue
        cur_emp = (content.get("currentEmployments") or [{}])
        cur0 = cur_emp[0] if cur_emp else {}
        first = (bi.get("firstName") or "").strip()
        last = (bi.get("lastName") or "").strip()
        if not first or not last: continue
        rows.append({
            "crd": str(crd), "first": first, "last": last,
            "full": f"{first} {last}",
            "aliases": bi.get("otherNames") or [],
            "firm": (cur0.get("firmName") or "").strip(),
            "city": (cur0.get("branchCity") or "").strip(),
        })
    print(f"active total: {active_total}, already-found-active: {active_total-len(rows)}, remaining: {len(rows)}", file=sys.stderr)

    for f in OUT_DIR.glob("chunk_*.json"): f.unlink()
    rows.sort(key=lambda r: r["crd"])
    chunk_size = (len(rows) + N_CHUNKS - 1) // N_CHUNKS
    for i in range(N_CHUNKS):
        chunk = rows[i*chunk_size:(i+1)*chunk_size]
        (OUT_DIR / f"chunk_{i:02d}.json").write_text(json.dumps(chunk))
    print(f"wrote {N_CHUNKS} chunks @ ~{chunk_size} brokers each", file=sys.stderr)

if __name__ == "__main__":
    main()
