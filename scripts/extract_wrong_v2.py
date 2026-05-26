#!/usr/bin/env python3
"""Wrong-person retry v2: include branch city + state for query disambiguation."""
import json, sqlite3, sys
from pathlib import Path

FINRA_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/finra_all_cache.db"
LI_DB = "/Users/mkt/Desktop/broker_linkedin_swarm/output/full_finra_dump/linkedin_cache.db"
N_CHUNKS = int(sys.argv[1]) if len(sys.argv)>1 else 100
OUT_DIR = Path(sys.argv[2]) if len(sys.argv)>2 else Path("chunks")
OUT_DIR.mkdir(exist_ok=True)


def main():
    li = sqlite3.connect(LI_DB)
    wrong = {r[0] for r in li.execute("SELECT crd FROM linkedin WHERE engine LIKE 'WRONG_PERSON%' AND (url IS NULL OR url='')")}
    print(f"wrong CRDs: {len(wrong)}", file=sys.stderr)
    conn = sqlite3.connect(FINRA_DB)
    rows = []
    for crd, data in conn.execute("SELECT crd, data FROM detail WHERE data IS NOT NULL"):
        if str(crd) not in wrong: continue
        try:
            c = json.loads(data)['hits']['hits'][0]['_source']['content']
            if isinstance(c,str): c = json.loads(c)
        except: continue
        bi = c.get('basicInformation') or {}
        first = (bi.get('firstName') or '').strip()
        last = (bi.get('lastName') or '').strip()
        middle = (bi.get('middleName') or '').strip()
        if not first or not last: continue
        cur = c.get('currentEmployments') or []
        firms = []
        city = ''
        state = ''
        if cur:
            cf = (cur[0].get('firmName') or '').strip()
            if cf: firms.append(cf)
            city = (cur[0].get('branchCity') or '').strip()
            state = (cur[0].get('branchState') or '').strip()
        for p in (c.get('previousEmployments') or [])[:2]:
            fn = (p.get('firmName') or '').strip()
            if fn and fn not in firms: firms.append(fn)
        rows.append({'crd':str(crd),'first':first,'last':last,'middle':middle,
                     'full':f'{first} {last}','firms':firms,'city':city,'state':state})
    print(f"pool: {len(rows)}", file=sys.stderr)
    for f in OUT_DIR.glob('chunk_*.json'): f.unlink()
    rows.sort(key=lambda r:r['crd'])
    cs = (len(rows)+N_CHUNKS-1)//N_CHUNKS
    for i in range(N_CHUNKS):
        ch = rows[i*cs:(i+1)*cs]
        (OUT_DIR/f'chunk_{i:02d}.json').write_text(json.dumps(ch))
    print(f"wrote {N_CHUNKS} chunks", file=sys.stderr)


if __name__=='__main__': main()
