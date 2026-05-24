#!/usr/bin/env python3
"""Extract VPS slice: 50k active brokers from the TAIL of remaining list,
so it overlaps least with GH chunks (which take the head)."""
import json
import sqlite3
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"
N = 50000

def main():
    li = sqlite3.connect(LI_DB)
    found = {r[0] for r in li.execute("SELECT crd FROM linkedin WHERE url LIKE 'http%'")}
    conn = sqlite3.connect(FINRA_DB)
    rows = []
    for crd, data_str in conn.execute("SELECT crd, data FROM detail WHERE data IS NOT NULL"):
        try:
            outer = json.loads(data_str)
            content = outer["hits"]["hits"][0]["_source"]["content"]
            if isinstance(content, str): content = json.loads(content)
        except Exception: continue
        bi = content.get("basicInformation") or {}
        if bi.get("bcScope") != "Active": continue
        if str(crd) in found: continue
        cur0 = (content.get("currentEmployments") or [{}])[0]
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
    rows.sort(key=lambda r: r["crd"], reverse=True)  # tail-first
    chunk = rows[:N]
    out = Path("/tmp/finra_li_matrix/vps_workload.json")
    out.write_text(json.dumps(chunk))
    print(f"wrote {len(chunk)} brokers (tail of remaining) -> {out}")

if __name__ == "__main__":
    main()
