#!/usr/bin/env python3
"""Chunker: all BC-Active brokers not yet w/ LinkedIn URL."""
import json
import sqlite3
import sys
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"

N_CHUNKS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("chunks")
OUT_DIR.mkdir(exist_ok=True)


def main():
    li = sqlite3.connect(LI_DB)
    found = {r[0] for r in li.execute("SELECT crd FROM linkedin WHERE url LIKE 'http%'")}
    print(f"already filled: {len(found)}", file=sys.stderr)

    conn = sqlite3.connect(FINRA_DB)
    rows = []
    for crd, data_str in conn.execute("SELECT crd, data FROM detail WHERE data IS NOT NULL"):
        if str(crd) in found:
            continue
        try:
            outer = json.loads(data_str)
            content = outer["hits"]["hits"][0]["_source"]["content"]
            if isinstance(content, str):
                content = json.loads(content)
        except Exception:
            continue
        bi = content.get("basicInformation") or {}
        bc = (bi.get("bcScope") or "").lower()
        ia = (bi.get("iaScope") or "").lower()
        if bc != "active" and ia != "active":
            continue
        first = (bi.get("firstName") or "").strip()
        last = (bi.get("lastName") or "").strip()
        if not first or not last:
            continue
        cur = content.get("currentEmployments") or []
        firms = []
        if cur:
            cf = (cur[0].get("firmName") or "").strip()
            if cf:
                firms.append(cf)
        for p in (content.get("previousEmployments") or [])[:3]:
            fn = (p.get("firmName") or "").strip()
            if fn and fn not in firms:
                firms.append(fn)
        rows.append({
            "crd": str(crd),
            "first": first,
            "last": last,
            "full": f"{first} {last}",
            "aliases": bi.get("otherNames") or [],
            "firms": firms,
        })

    print(f"BC-Active unfilled: {len(rows)}", file=sys.stderr)

    for f in OUT_DIR.glob("chunk_*.json"):
        f.unlink()

    rows.sort(key=lambda r: r["crd"])
    chunk_size = (len(rows) + N_CHUNKS - 1) // N_CHUNKS
    for i in range(N_CHUNKS):
        chunk = rows[i * chunk_size:(i + 1) * chunk_size]
        out = OUT_DIR / f"chunk_{i:02d}.json"
        out.write_text(json.dumps(chunk))
    print(f"wrote {N_CHUNKS} chunks @ ~{chunk_size} brokers each", file=sys.stderr)


if __name__ == "__main__":
    main()
