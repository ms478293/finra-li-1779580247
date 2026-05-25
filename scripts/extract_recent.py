#!/usr/bin/env python3
"""Pass D chunker: brokers w/ current employment registered 2024+ but no LinkedIn URL.

Output 40 JSON chunks for GH matrix. Each row: crd, first, last, full, aliases, firms[].
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"

N_CHUNKS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
MIN_YEAR = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
OUT_DIR = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("chunks")
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
        cur = content.get("currentEmployments") or []
        if not cur:
            continue
        date = cur[0].get("registrationBeginDate") or ""
        m = re.search(r"(20\d{2}|19\d{2})", date)
        if not m or int(m.group(1)) < MIN_YEAR:
            continue
        bi = content.get("basicInformation") or {}
        first = (bi.get("firstName") or "").strip()
        last = (bi.get("lastName") or "").strip()
        if not first or not last:
            continue
        firms = []
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
            "year": int(m.group(1)),
        })

    print(f"recent-hire ({MIN_YEAR}+) unfilled: {len(rows)}", file=sys.stderr)

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
