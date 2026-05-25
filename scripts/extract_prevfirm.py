#!/usr/bin/env python3
"""Pass B chunker: emit brokers w/ previous-firm history for novel queries.

Each broker row carries up to 4 firm candidates (current + up to 3 prev).
Search script will try them in order — previous firm matches stale LinkedIn
profiles that didn't surface via current-firm queries.
"""
import json
import sqlite3
import sys
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"

N_CHUNKS = int(sys.argv[1]) if len(sys.argv) > 1 else 40
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("chunks")
OUT_DIR.mkdir(exist_ok=True)


def main():
    li_conn = sqlite3.connect(LI_DB)
    found = {r[0] for r in li_conn.execute("SELECT crd FROM linkedin WHERE url LIKE 'http%'")}
    print(f"already found: {len(found)}", file=sys.stderr)

    conn = sqlite3.connect(FINRA_DB)
    rows = []
    skipped_no_prev = 0
    for crd, data_str in conn.execute("SELECT crd, data FROM detail WHERE data IS NOT NULL"):
        if str(crd) in found:
            continue
        try:
            outer = json.loads(data_str)
            src = outer["hits"]["hits"][0]["_source"]
            content = src["content"]
            if isinstance(content, str):
                content = json.loads(content)
        except Exception:
            continue
        bi = content.get("basicInformation") or {}
        cur_emp = content.get("currentEmployments") or []
        prev_emp = content.get("previousEmployments") or []

        first = (bi.get("firstName") or "").strip()
        last = (bi.get("lastName") or "").strip()
        if not first or not last:
            continue

        firms = []
        cur_firm = (cur_emp[0].get("firmName") if cur_emp else "") or ""
        if cur_firm:
            firms.append(cur_firm.strip())
        for p in prev_emp[:5]:
            fn = (p.get("firmName") or "").strip()
            if fn and fn not in firms:
                firms.append(fn)
            if len(firms) >= 4:
                break

        # Skip rows w/ no firm history at all
        if not firms:
            skipped_no_prev += 1
            continue

        rows.append({
            "crd": str(crd),
            "first": first,
            "last": last,
            "full": f"{first} {last}",
            "aliases": bi.get("otherNames") or [],
            "firms": firms,
            "city": (cur_emp[0].get("branchCity") if cur_emp else "") or "",
        })
    print(f"remaining: {len(rows)} (skipped {skipped_no_prev} no-firm)", file=sys.stderr)

    # Wipe old chunks
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
