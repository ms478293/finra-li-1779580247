#!/usr/bin/env python3
"""4-engine LinkedIn scraper: DDG-html (uddg unwrap) + Bing + Yandex + Startpage. Loose match."""
import asyncio, csv, json, os, re, sys, time, unicodedata, urllib.parse
import httpx

CHUNK = sys.argv[1]; OUT = sys.argv[2]
LRE = re.compile(r'linkedin\.com/in/[a-zA-Z0-9_\-]+', re.I)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
CONCURRENCY = 25

def norm(s):
    s = ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '', s.lower())

def url_slug(u):
    m = re.search(r'linkedin\.com/in/([^/?#\s]+)', u or '', re.I)
    return norm(m.group(1)) if m else ''

def slug_loose_match(slug, first, last, aliases=None):
    if not slug: return False
    f = norm(first); l = norm(last)
    if not l: return False
    if l in slug or (len(l) >= 4 and l[:5] in slug and f and f[0] in slug): return True
    if f and len(f) >= 4 and f in slug: return True
    for a in (aliases or []):
        an = norm(a)
        if an and len(an) >= 6 and an in slug: return True
    return False

async def s_ddg(c, q):
    try:
        r = await c.get(f'https://html.duckduckgo.com/html/?q={urllib.parse.quote(q)}')
        if r.status_code != 200: return r.status_code, []
        urls = set()
        for e in re.findall(r'uddg=([^&"]+)', r.text):
            d = urllib.parse.unquote(e)
            m = LRE.search(d)
            if m: urls.add(m.group())
        urls.update(LRE.findall(r.text))
        return 200, list(urls)
    except: return -1, []

async def s_bing(c, q):
    try:
        r = await c.get(f'https://www.bing.com/search?q={urllib.parse.quote(q)}')
        if r.status_code != 200: return r.status_code, []
        urls = set(LRE.findall(r.text))
        import base64
        for u in re.findall(r'u=a1([A-Za-z0-9_-]+)', r.text):
            try:
                d = base64.urlsafe_b64decode(u + '==').decode('utf8', errors='ignore')
                for m in LRE.findall(d): urls.add(m)
            except: pass
        return 200, list(urls)
    except: return -1, []

async def s_yandex(c, q):
    try:
        r = await c.get(f'https://yandex.com/search/?text={urllib.parse.quote(q)}', timeout=15)
        if r.status_code != 200: return r.status_code, []
        return 200, list(set(LRE.findall(r.text)))
    except: return -1, []

async def s_startpage(c, q):
    try:
        r = await c.get(f'https://www.startpage.com/sp/search?q={urllib.parse.quote(q)}')
        if r.status_code != 200: return r.status_code, []
        return 200, list(set(LRE.findall(r.text)))
    except: return -1, []

ENGINES = [('ddg', s_ddg), ('bing', s_bing), ('yandex', s_yandex), ('startpage', s_startpage)]

async def search_broker(c, b, sem, throttle):
    full = b["full"]; firm = b.get("firm","") or ""
    firm_short = firm.split()[0] if firm else ""
    queries = [f'"{full}" {firm_short} linkedin' if firm_short else f'"{full}" linkedin', f'"{full}" linkedin']
    async with sem:
        for q in queries:
            for ename, fn in ENGINES:
                if time.monotonic() < throttle.get(ename, 0): continue
                code, urls = await fn(c, q)
                if code == 429 or code == 403:
                    throttle[ename] = time.monotonic() + 45
                    continue
                if not urls: continue
                for u in urls:
                    slug = url_slug(u)
                    if slug_loose_match(slug, b['first'], b['last'], b.get('aliases') or []):
                        clean = re.search(r'linkedin\.com/in/[a-zA-Z0-9_\-]+', u).group()
                        return b['crd'], 'https://' + clean.rstrip('/'), q, ename
    return b['crd'], None, queries[-1], ""

async def main():
    brokers = json.loads(open(CHUNK).read())
    print(f"chunk {CHUNK}: {len(brokers)} brokers, conc={CONCURRENCY}", flush=True)
    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f): done[r['crd']] = (r.get('linkedin_url',''), r.get('engine',''))
    hdr = {'User-Agent': UA, 'Accept-Language':'en-US,en;q=0.9', 'Accept-Encoding':'gzip, deflate'}
    async with httpx.AsyncClient(timeout=20, headers=hdr, follow_redirects=True, http2=False,
                                  limits=httpx.Limits(max_connections=CONCURRENCY*2)) as c:
        sem = asyncio.Semaphore(CONCURRENCY); throttle = {}
        fieldnames = ['crd','linkedin_url','query','engine']
        f = open(OUT,'w',newline=''); w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader()
        for crd, (url, engine) in done.items():
            w.writerow({'crd':crd,'linkedin_url':url,'query':'','engine':engine})
        todo = [b for b in brokers if b['crd'] not in done]
        hits = [sum(1 for v in done.values() if v[0])]; completed = [0]
        t0 = time.monotonic(); lock = asyncio.Lock()
        async def runner(b):
            crd, url, q, e = await search_broker(c, b, sem, throttle)
            async with lock:
                w.writerow({'crd':crd,'linkedin_url':url or '','query':q,'engine':e})
                completed[0] += 1
                if url: hits[0] += 1
                if completed[0] % 200 == 0:
                    f.flush()
                    el = time.monotonic()-t0
                    print(f"  {completed[0]}/{len(todo)} hits={hits[0]} ({100*hits[0]/completed[0]:.0f}%) rate={completed[0]/el:.1f}/s", flush=True)
        await asyncio.gather(*[runner(b) for b in todo])
        f.close()
        print(f"DONE: {hits[0]}/{len(todo)} in {time.monotonic()-t0:.0f}s", flush=True)

asyncio.run(main())
