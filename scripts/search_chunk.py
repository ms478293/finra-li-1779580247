#!/usr/bin/env python3
"""Per-runner LinkedIn URL search via DDGS w/ name validation.

Reads chunks/chunk_NN.json (list of brokers), outputs out_NN.csv with crd,linkedin_url,query.
Resume-able: skips already-found rows from any prior partial output.
"""
import csv
import json
import os
import random
import re
import sys
import time
import unicodedata

from ddgs import DDGS

CHUNK = sys.argv[1]
OUT = sys.argv[2]

URL_RE = re.compile(r'https?://[a-z\.]*linkedin\.com/in/[^\s\?&"\'<>)]+', re.I)


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(s or '')) if not unicodedata.combining(c))


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', strip_accents(s).lower())


def clean_url(u):
    m = URL_RE.search(u or '')
    if not m:
        return None
    return m.group(0).rstrip('/').split('?')[0].split('#')[0]


def url_slug(u):
    m = re.search(r'linkedin\.com/in/([^/?#]+)', u or '', re.I)
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
    bt = norm(body_text)
    if bt and l in slug and l in bt and len(f) >= 1 and f[:1] in slug:
        return True
    for a in (aliases or []):
        an = norm(a)
        if len(an) >= 6 and an[-3:] in slug and an[0] in slug:
            return True
    return False


def search_broker(ddgs, b):
    queries = [
        f'"{b["full"]}" "{b["firm"]}" site:linkedin.com/in' if b.get("firm") else f'"{b["full"]}" site:linkedin.com/in',
        f'"{b["full"]}" site:linkedin.com/in',
        f'{b["full"]} {b.get("firm","")} linkedin'.strip(),
    ]
    for q in queries:
        try:
            results = list(ddgs.text(q, max_results=8))
        except Exception as e:
            msg = str(e)
            if 'Ratelimit' in msg or '202' in msg or '429' in msg:
                time.sleep(20 + random.uniform(0, 10))
                continue
            time.sleep(2)
            continue
        for r in results:
            href = r.get('href') or ''
            if 'linkedin.com/in/' not in href:
                continue
            u = clean_url(href)
            if not u:
                continue
            body = (r.get('title', '') or '') + ' ' + (r.get('body', '') or '')
            if slug_matches(u, b['first'], b['last'], b.get('aliases') or [], body):
                return u, q
        time.sleep(0.3 + random.uniform(0, 0.4))
    return None, queries[-1]


def main():
    brokers = json.loads(open(CHUNK).read())
    print(f"chunk {CHUNK}: {len(brokers)} brokers", flush=True)

    # resume support
    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                done[r['crd']] = r.get('linkedin_url', '')

    ddgs = DDGS()
    fieldnames = ['crd', 'linkedin_url', 'query']
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for crd, url in done.items():
            w.writerow({'crd': crd, 'linkedin_url': url, 'query': ''})

        hits = sum(1 for v in done.values() if v)
        t0 = time.monotonic()
        for i, b in enumerate(brokers, 1):
            if b['crd'] in done:
                continue
            u, q = search_broker(ddgs, b)
            w.writerow({'crd': b['crd'], 'linkedin_url': u or '', 'query': q})
            if u:
                hits += 1
            if i % 100 == 0:
                f.flush()
                el = time.monotonic() - t0
                pct = 100 * hits / i
                print(f"  {i}/{len(brokers)} hits={hits} ({pct:.0f}%) rate={i/el:.1f}/s", flush=True)
            time.sleep(0.4 + random.uniform(0, 0.3))


if __name__ == "__main__":
    main()
