#!/usr/bin/env python3
"""LinkedIn URL search via Startpage (primary) + DDGS (fallback).

Startpage = Google proxy, doesn't aggressively rate-limit per IP.
Falls back to DDGS only if Startpage 429s or returns no results.
"""
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse

import httpx
from ddgs import DDGS

CHUNK = sys.argv[1]
OUT = sys.argv[2]

LRE = re.compile(r'linkedin\.com/in/[a-zA-Z0-9_\-]+', re.I)
URL_RE = re.compile(r'https?://[a-z\.]*linkedin\.com/in/[^\s\?&"\'<>)]+', re.I)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c))


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', strip_accents(s).lower())


def clean_url(u):
    m = URL_RE.search(u or '')
    if not m:
        m2 = LRE.search(u or '')
        if not m2: return None
        return ("https://" + m2.group(0)).rstrip('/').split('?')[0].split('#')[0]
    return m.group(0).rstrip('/').split('?')[0].split('#')[0]


def url_slug(u):
    m = re.search(r'linkedin\.com/in/([^/?#\s]+)', u or '', re.I)
    return norm(m.group(1)) if m else ''


def slug_matches(u, first, last, aliases=None, body_text=''):
    slug = url_slug(u)
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


def search_startpage(client, q):
    """Returns (status, list_of_urls)."""
    try:
        r = client.get(f'https://www.startpage.com/sp/search?query={urllib.parse.quote(q)}')
        if r.status_code != 200:
            return r.status_code, []
        urls = list(set(LRE.findall(r.text)))
        return 200, urls
    except Exception:
        return -1, []


def search_brave(client, q):
    try:
        r = client.get(f'https://search.brave.com/search?q={urllib.parse.quote(q)}&source=web')
        if r.status_code != 200:
            return r.status_code, []
        urls = list(set(LRE.findall(r.text)))
        return 200, urls
    except Exception:
        return -1, []


def search_ddgs(ddgs, q):
    try:
        results = list(ddgs.text(q, max_results=8))
    except Exception:
        return -1, []
    out = []
    for r in results:
        href = r.get('href') or ''
        if 'linkedin.com/in/' in href:
            out.append(href)
    return 200, list(set(out))


def search_broker(client, ddgs, b, throttle):
    full = b["full"]
    firm = b.get("firm", "") or ""
    queries = [
        f'"{full}" "{firm}" linkedin' if firm else f'"{full}" linkedin',
        f'"{full}" site:linkedin.com/in',
        f'{full} {firm.split()[0] if firm else ""} linkedin',
    ]

    for q in queries:
        # Startpage primary
        if time.monotonic() > throttle['sp']:
            code, urls = search_startpage(client, q)
            if code == 429:
                throttle['sp'] = time.monotonic() + 45
            elif urls:
                for u in urls:
                    cu = clean_url(u)
                    if cu and slug_matches(cu, b['first'], b['last'], b.get('aliases') or []):
                        return cu, q, "startpage"

        # Brave fallback
        if time.monotonic() > throttle['brave']:
            code, urls = search_brave(client, q)
            if code == 429:
                throttle['brave'] = time.monotonic() + 60
            elif urls:
                for u in urls:
                    cu = clean_url(u)
                    if cu and slug_matches(cu, b['first'], b['last'], b.get('aliases') or []):
                        return cu, q, "brave"

        time.sleep(0.4 + random.uniform(0, 0.3))

    # DDGS last resort
    if time.monotonic() > throttle['ddgs']:
        code, urls = search_ddgs(ddgs, f'"{full}" linkedin')
        if code == 429 or code == -1:
            throttle['ddgs'] = time.monotonic() + 120
        elif urls:
            for u in urls:
                cu = clean_url(u)
                if cu and slug_matches(cu, b['first'], b['last'], b.get('aliases') or []):
                    return cu, queries[0], "ddgs"

    return None, queries[-1], ""


def main():
    brokers = json.loads(open(CHUNK).read())
    print(f"chunk {CHUNK}: {len(brokers)} brokers", flush=True)

    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                done[r['crd']] = (r.get('linkedin_url', ''), r.get('engine', ''))

    hdr = {'User-Agent': UA, 'Accept-Language': 'en-US,en;q=0.9', 'Accept-Encoding': 'gzip, deflate'}
    client = httpx.Client(timeout=20, headers=hdr, follow_redirects=True, http2=False)
    ddgs = DDGS()
    throttle = {'sp': 0.0, 'brave': 0.0, 'ddgs': 0.0}

    fieldnames = ['crd', 'linkedin_url', 'query', 'engine']
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for crd, (url, engine) in done.items():
            w.writerow({'crd': crd, 'linkedin_url': url, 'query': '', 'engine': engine})

        hits = sum(1 for v in done.values() if v[0])
        eng_count = {'startpage': 0, 'brave': 0, 'ddgs': 0}
        t0 = time.monotonic()
        for i, b in enumerate(brokers, 1):
            if b['crd'] in done:
                continue
            u, q, eng = search_broker(client, ddgs, b, throttle)
            w.writerow({'crd': b['crd'], 'linkedin_url': u or '', 'query': q, 'engine': eng})
            if u:
                hits += 1
                eng_count[eng] = eng_count.get(eng, 0) + 1
            if i % 100 == 0:
                f.flush()
                el = time.monotonic() - t0
                pct = 100 * hits / i
                print(f"  {i}/{len(brokers)} hits={hits} ({pct:.0f}%) "
                      f"[sp={eng_count['startpage']} brave={eng_count['brave']} ddg={eng_count['ddgs']}] "
                      f"{i/el:.1f}/s", flush=True)
            time.sleep(0.3 + random.uniform(0, 0.2))


if __name__ == "__main__":
    main()
