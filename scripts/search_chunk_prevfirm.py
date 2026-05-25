#!/usr/bin/env python3
"""Pass B search: try Full Name + each firm candidate (current → prev1 → prev2 → prev3).

Async, concurrency=20. Startpage primary + Brave fallback (same as Pass A).
Stops at first slug-validated linkedin.com/in URL.
"""
import asyncio
import csv
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse

import httpx

CHUNK = sys.argv[1]
OUT = sys.argv[2]

LRE = re.compile(r'linkedin\.com/in/[a-zA-Z0-9_\-]+', re.I)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
CONCURRENCY = 20


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c))


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', strip_accents(s).lower())


def url_slug(u):
    m = re.search(r'linkedin\.com/in/([^/?#\s]+)', u or '', re.I)
    return norm(m.group(1)) if m else ''


def slug_matches(slug, first, last, aliases=None):
    if not slug:
        return False
    f = norm(first); l = norm(last)
    if not l:
        return False
    if len(f) >= 2 and len(l) >= 3 and f[:2] in slug and l in slug:
        return True
    if len(f) >= 1 and len(l) >= 4 and f[:1] in slug and l in slug:
        return True
    fn = norm(f"{first} {last}")
    if len(fn) >= 8 and fn in slug:
        return True
    for a in (aliases or []):
        an = norm(a)
        if len(an) >= 6 and an[-3:] in slug and an[0] in slug:
            return True
    return False


async def search_startpage(client, q):
    try:
        r = await client.get(f'https://www.startpage.com/sp/search?q={urllib.parse.quote(q)}')
        if r.status_code != 200:
            return r.status_code, []
        return 200, list(set(LRE.findall(r.text)))
    except Exception:
        return -1, []


async def search_brave(client, q):
    try:
        r = await client.get(f'https://search.brave.com/search?q={urllib.parse.quote(q)}&source=web')
        if r.status_code != 200:
            return r.status_code, []
        return 200, list(set(LRE.findall(r.text)))
    except Exception:
        return -1, []


async def try_query(client, q, b, throttle):
    if time.monotonic() > throttle['sp']:
        code, urls = await search_startpage(client, q)
        if code == 429:
            throttle['sp'] = time.monotonic() + 30
        else:
            for u in urls:
                if slug_matches(url_slug(u), b['first'], b['last'], b.get('aliases') or []):
                    return "https://" + u.rstrip('/'), "startpage"
    if time.monotonic() > throttle['brave']:
        code, urls = await search_brave(client, q)
        if code == 429:
            throttle['brave'] = time.monotonic() + 45
        else:
            for u in urls:
                if slug_matches(url_slug(u), b['first'], b['last'], b.get('aliases') or []):
                    return "https://" + u.rstrip('/'), "brave"
    return None, ""


async def search_broker(client, b, sem, throttle):
    full = b["full"]
    firms = b.get("firms") or []
    # Build query list: each firm's first token + full name
    queries = []
    for firm in firms:
        ft = firm.split()[0] if firm else ""
        if ft:
            queries.append(f'{full} {ft} linkedin')
    # Final fallback: name-only
    queries.append(f'{full} linkedin')

    async with sem:
        for q in queries:
            url, engine = await try_query(client, q, b, throttle)
            if url:
                return b['crd'], url, q, engine
    return b['crd'], None, queries[-1], ""


async def main():
    brokers = json.loads(open(CHUNK).read())
    print(f"chunk {CHUNK}: {len(brokers)} brokers, conc={CONCURRENCY}", flush=True)

    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                done[r['crd']] = (r.get('linkedin_url', ''), r.get('engine', ''))

    hdr = {'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9', 'Accept-Encoding': 'gzip, deflate'}
    async with httpx.AsyncClient(
        timeout=20, headers=hdr, follow_redirects=True, http2=False,
        limits=httpx.Limits(max_connections=CONCURRENCY*2, max_keepalive_connections=CONCURRENCY)
    ) as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        throttle = {'sp': 0.0, 'brave': 0.0}

        fieldnames = ['crd', 'linkedin_url', 'query', 'engine']
        f = open(OUT, 'w', newline='')
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for crd, (url, engine) in done.items():
            w.writerow({'crd': crd, 'linkedin_url': url, 'query': '', 'engine': engine})

        todo = [b for b in brokers if b['crd'] not in done]
        hits = [sum(1 for v in done.values() if v[0])]
        eng = {'startpage': 0, 'brave': 0}
        completed = [0]
        t0 = time.monotonic()
        lock = asyncio.Lock()

        async def runner(b):
            crd, url, q, e = await search_broker(client, b, sem, throttle)
            async with lock:
                w.writerow({'crd': crd, 'linkedin_url': url or '', 'query': q, 'engine': e})
                completed[0] += 1
                if url:
                    hits[0] += 1
                    eng[e] = eng.get(e, 0) + 1
                if completed[0] % 200 == 0:
                    f.flush()
                    el = time.monotonic() - t0
                    print(f"  {completed[0]}/{len(todo)} hits={hits[0]} ({100*hits[0]/max(completed[0],1):.0f}%) "
                          f"sp={eng['startpage']} br={eng['brave']} rate={completed[0]/el:.1f}/s", flush=True)

        await asyncio.gather(*[runner(b) for b in todo])
        f.close()
        el = time.monotonic() - t0
        print(f"DONE: {hits[0]}/{len(todo)} in {el:.1f}s", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
