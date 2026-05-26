#!/usr/bin/env python3
"""Strict-mode search: requires BOTH full first AND full last in LinkedIn slug.
Used for wrong-person retry where slug-prefix match got fooled."""
import asyncio, csv, json, os, re, sys, time, unicodedata, urllib.parse
import httpx

CHUNK = sys.argv[1]
OUT = sys.argv[2]

LRE = re.compile(r'linkedin\.com/in/[a-zA-Z0-9_\-]+', re.I)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
CONCURRENCY = 30


def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '', s.lower())


def url_slug(u):
    m = re.search(r'linkedin\.com/in/([^/?#\s]+)', u or '', re.I)
    return norm(m.group(1)) if m else ''


def slug_matches_strict(slug, first, last, aliases=None):
    """STRICT: require full first AND full last name in slug. No prefix shortcuts."""
    if not slug: return False
    f = norm(first); l = norm(last)
    if len(f) < 3 or len(l) < 3: return False
    return f in slug and l in slug


async def search_startpage(client, q):
    try:
        r = await client.get(f'https://www.startpage.com/sp/search?q={urllib.parse.quote(q)}')
        return (r.status_code, list(set(LRE.findall(r.text)))) if r.status_code==200 else (r.status_code, [])
    except: return -1, []


async def search_brave(client, q):
    try:
        r = await client.get(f'https://search.brave.com/search?q={urllib.parse.quote(q)}&source=web')
        return (r.status_code, list(set(LRE.findall(r.text)))) if r.status_code==200 else (r.status_code, [])
    except: return -1, []


async def try_query(client, q, b, throttle):
    if time.monotonic() > throttle['sp']:
        code, urls = await search_startpage(client, q)
        if code == 429: throttle['sp'] = time.monotonic() + 30
        else:
            for u in urls:
                if slug_matches_strict(url_slug(u), b['first'], b['last']):
                    return "https://" + u.rstrip('/'), "startpage"
    if time.monotonic() > throttle['brave']:
        code, urls = await search_brave(client, q)
        if code == 429: throttle['brave'] = time.monotonic() + 45
        else:
            for u in urls:
                if slug_matches_strict(url_slug(u), b['first'], b['last']):
                    return "https://" + u.rstrip('/'), "brave"
    return None, ""


async def search_broker(client, b, sem, throttle):
    full = b['full']
    firms = b.get('firms') or []
    queries = []
    for firm in firms:
        ft = firm.split()[0] if firm else ""
        if ft: queries.append(f'"{full}" {ft} site:linkedin.com/in')
    queries.append(f'"{full}" site:linkedin.com/in')
    async with sem:
        for q in queries:
            url, engine = await try_query(client, q, b, throttle)
            if url: return b['crd'], url, q, engine
    return b['crd'], None, queries[-1] if queries else '', ""


async def main():
    brokers = json.loads(open(CHUNK).read())
    print(f"chunk {CHUNK}: {len(brokers)} brokers", flush=True)
    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                done[r['crd']] = (r.get('linkedin_url',''), r.get('engine',''))
    hdr = {'User-Agent':UA,'Accept-Language':'en-US,en;q=0.9','Accept-Encoding':'gzip, deflate'}
    async with httpx.AsyncClient(timeout=20, headers=hdr, follow_redirects=True, http2=False,
        limits=httpx.Limits(max_connections=CONCURRENCY*2, max_keepalive_connections=CONCURRENCY)) as client:
        sem = asyncio.Semaphore(CONCURRENCY)
        throttle = {'sp':0.0, 'brave':0.0}
        fieldnames = ['crd','linkedin_url','query','engine']
        f = open(OUT,'w', newline=''); w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader()
        for crd,(url,engine) in done.items():
            w.writerow({'crd':crd,'linkedin_url':url,'query':'','engine':engine})
        todo = [b for b in brokers if b['crd'] not in done]
        hits=[sum(1 for v in done.values() if v[0])]; completed=[0]; t0=time.monotonic(); lock=asyncio.Lock()
        async def runner(b):
            crd,url,q,e = await search_broker(client, b, sem, throttle)
            async with lock:
                w.writerow({'crd':crd,'linkedin_url':url or '','query':q,'engine':e})
                completed[0]+=1
                if url: hits[0]+=1
                if completed[0]%200==0:
                    f.flush()
                    el=time.monotonic()-t0
                    print(f"  {completed[0]}/{len(todo)} hits={hits[0]} rate={completed[0]/el:.1f}/s",flush=True)
        await asyncio.gather(*[runner(b) for b in todo])
        f.close()
        print(f"DONE: {hits[0]}/{len(todo)} strict matches",flush=True)


if __name__=="__main__":
    asyncio.run(main())
