#!/usr/bin/env python3
"""LinkedIn URL search w/ multi-engine fallback. Per-runner script for GH matrix.

Engines in order:
  1. DDGS (ddgs Python lib) - primary
  2. Brave HTML scrape - fallback when DDGS throttles
  3. Mojeek HTML scrape - last resort

Reads chunks/chunk_NN.json, outputs out_NN.csv (resume-able).
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

import urllib.request
from ddgs import DDGS

CHUNK = sys.argv[1]
OUT = sys.argv[2]

URL_RE = re.compile(r'https?://[a-z\.]*linkedin\.com/in/[^\s\?&"\'<>)]+', re.I)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


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


def fetch_html(url, timeout=15):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""


# ------------------ Engine implementations ------------------

def search_ddgs(ddgs, q):
    """Returns list of (url, title+body) or raises on rate-limit."""
    try:
        results = list(ddgs.text(q, max_results=8))
    except Exception as e:
        msg = str(e)
        if 'Ratelimit' in msg or '202' in msg or '429' in msg:
            return None  # signal throttle
        return []
    out = []
    for r in results:
        href = r.get('href') or ''
        if 'linkedin.com/in/' in href:
            ft = (r.get('title', '') or '') + ' ' + (r.get('body', '') or '')
            out.append((href, ft))
    return out


def search_brave_html(q):
    """Scrape Brave Search HTML. Returns list of (url, title+body)."""
    url = f"https://search.brave.com/search?q={urllib.parse.quote(q)}"
    html = fetch_html(url)
    if not html or "captcha" in html.lower():
        return None
    out = []
    for m in URL_RE.finditer(html):
        u = m.group(0)
        # body context: surrounding 200 chars
        idx = m.start()
        ctx = html[max(0, idx-150):idx+200]
        # strip HTML tags
        ctx = re.sub(r'<[^>]+>', ' ', ctx)
        out.append((u, ctx))
    return out


def search_mojeek(q):
    """Scrape Mojeek HTML. Returns list of (url, title+body)."""
    url = f"https://www.mojeek.com/search?q={urllib.parse.quote(q)}"
    html = fetch_html(url)
    if not html or "blocked" in html.lower():
        return None
    out = []
    for m in URL_RE.finditer(html):
        u = m.group(0)
        idx = m.start()
        ctx = html[max(0, idx-150):idx+200]
        ctx = re.sub(r'<[^>]+>', ' ', ctx)
        out.append((u, ctx))
    return out


# ------------------ Search orchestration ------------------

def search_broker(ddgs, b, ddgs_throttle_until):
    full = b["full"]
    firm = b.get("firm", "") or ""
    queries = [
        f'"{full}" "{firm}" site:linkedin.com/in' if firm else f'"{full}" site:linkedin.com/in',
        f'"{full}" site:linkedin.com/in',
        f'{full} {firm} linkedin'.strip(),
    ]

    for q in queries:
        # Engine 1: DDGS (unless throttled)
        if time.monotonic() > ddgs_throttle_until[0]:
            res = search_ddgs(ddgs, q)
            if res is None:
                # throttled — back off DDGS for 60s
                ddgs_throttle_until[0] = time.monotonic() + 60
                print(f"  [DDGS throttle, backing off 60s]", flush=True)
            else:
                for u, ft in res:
                    cu = clean_url(u)
                    if cu and slug_matches(cu, b['first'], b['last'], b.get('aliases') or [], ft):
                        return cu, q, "ddgs"
                time.sleep(0.3 + random.uniform(0, 0.3))
                continue

        # Engine 2: Brave HTML
        res = search_brave_html(q)
        if res:
            for u, ft in res:
                cu = clean_url(u)
                if cu and slug_matches(cu, b['first'], b['last'], b.get('aliases') or [], ft):
                    return cu, q, "brave"
        else:
            time.sleep(1)

        # Engine 3: Mojeek
        res = search_mojeek(q)
        if res:
            for u, ft in res:
                cu = clean_url(u)
                if cu and slug_matches(cu, b['first'], b['last'], b.get('aliases') or [], ft):
                    return cu, q, "mojeek"

        time.sleep(0.5)

    return None, queries[-1], ""


def main():
    brokers = json.loads(open(CHUNK).read())
    print(f"chunk {CHUNK}: {len(brokers)} brokers", flush=True)

    done = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            for r in csv.DictReader(f):
                done[r['crd']] = (r.get('linkedin_url', ''), r.get('engine', ''))

    ddgs = DDGS()
    ddgs_throttle_until = [0.0]  # mutable for closures

    fieldnames = ['crd', 'linkedin_url', 'query', 'engine']
    with open(OUT, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for crd, (url, engine) in done.items():
            w.writerow({'crd': crd, 'linkedin_url': url, 'query': '', 'engine': engine})

        hits = sum(1 for v in done.values() if v[0])
        ddgs_hits = brave_hits = mojeek_hits = 0
        t0 = time.monotonic()
        for i, b in enumerate(brokers, 1):
            if b['crd'] in done:
                continue
            u, q, eng = search_broker(ddgs, b, ddgs_throttle_until)
            w.writerow({'crd': b['crd'], 'linkedin_url': u or '', 'query': q, 'engine': eng})
            if u:
                hits += 1
                if eng == "ddgs": ddgs_hits += 1
                elif eng == "brave": brave_hits += 1
                elif eng == "mojeek": mojeek_hits += 1
            if i % 100 == 0:
                f.flush()
                el = time.monotonic() - t0
                pct = 100 * hits / i
                print(f"  {i}/{len(brokers)} hits={hits} ({pct:.0f}%) "
                      f"[ddg={ddgs_hits} brave={brave_hits} moj={mojeek_hits}] {i/el:.1f}/s",
                      flush=True)


if __name__ == "__main__":
    main()
