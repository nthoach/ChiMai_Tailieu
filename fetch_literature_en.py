#!/usr/bin/env python3
"""Fetch literature using the English keyword section from keywords.md and download PDFs.

Writes/updates: urls.txt, metadata.csv, summary.csv and downloads PDFs into References/.
"""
import argparse
import shutil
import glob
import sqlite3
import csv
import json
import os
import re
import sys
import time
from urllib import parse, request, error

try:
    import requests
except ImportError:
    print('requests library not found. Install with: pip install requests')
    sys.exit(1)

BASE_DIR = os.path.dirname(__file__)
KW_FILE = os.path.join(BASE_DIR, 'keywords.md')
URLS_FILE = os.path.join(BASE_DIR, 'urls.txt')
METADATA_CSV = os.path.join(BASE_DIR, 'metadata.csv')
SUMMARY_CSV = os.path.join(BASE_DIR, 'summary.csv')
REF_DIR = os.path.join(BASE_DIR, 'References')

os.makedirs(REF_DIR, exist_ok=True)

def extract_english_keywords():
    lines = []
    in_eng = False
    with open(KW_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if s.lower().startswith('## english'):
                in_eng = True
                continue
            if in_eng:
                if s.startswith('## '):
                    break
                if not s or s.startswith('#'):
                    continue
                if s.startswith('-'):
                    s = s[1:].strip()
                lines.append(s)
    return lines

def sanitize_filename(s):
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^0-9A-Za-z_\-\.]+', '', s)
    return s[:200]

def append_urls(url, preferred):
    with open(URLS_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{url}\t{preferred}\n")

def append_metadata(row):
    with open(METADATA_CSV, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if os.path.getsize(METADATA_CSV) == 0:
            writer.writerow(['filename','title','authors','year','journal','doi','url','abstract','language'])
        writer.writerow(row)

def append_summary(row):
    with open(SUMMARY_CSV, 'a', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        if os.path.getsize(SUMMARY_CSV) == 0:
            writer.writerow(['filename','objective','methods','main_findings','relevance_notes'])
        writer.writerow(row)

def query_crossref(query, rows=100):
    q = parse.quote(query)
    url = f'https://api.crossref.org/works?query.title={q}&rows={rows}'
    try:
        with request.urlopen(url, timeout=30) as resp:
            data = resp.read()
            return json.loads(data.decode('utf-8'))
    except error.URLError as e:
        print('CrossRef query error:', e)
        return None

def download_file(url, outpath, cookie_header=None, timeout=60):
    """Download a URL to outpath. If cookie_header is provided, include it."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/117.0.0.0 Safari/537.36'
    }
    if cookie_header:
        headers['Cookie'] = cookie_header
    try:
        req = request.Request(url, headers=headers)
        with request.urlopen(req, timeout=timeout) as resp:
            with open(outpath, 'wb') as f:
                f.write(resp.read())
        return True
    except Exception as e:
        print('Download failed for', url, ':', e)
        return False


def load_cookies_netscape(path, domain_filter=None):
    """Load cookies from a Netscape-format cookies.txt file and return a Cookie header string.
    If domain_filter is set, only include cookies for that domain or its subdomains.
    """
    if not os.path.exists(path):
        return ''
    parts = []
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            cols = line.strip().split('\t')
            if len(cols) >= 7:
                domain = cols[0]
                name = cols[5]
                value = cols[6]
                if domain_filter:
                    if domain_filter not in domain and not domain.endswith(domain_filter):
                        continue
                parts.append(f"{name}={value}")
    return '; '.join(parts)


def get_chrome_cookies(domain):
    """Try to extract cookies for domain from Chrome/Chromium profile (Linux)."""
    # Try common Chrome/Chromium locations
    home = os.path.expanduser('~')
    candidates = [
        os.path.join(home, '.config', 'google-chrome', 'Default', 'Cookies'),
        os.path.join(home, '.config', 'chromium', 'Default', 'Cookies'),
    ]
    for db_path in candidates:
        if os.path.exists(db_path):
            # Copy to temp to avoid lock
            tmp = db_path + '.tmp'
            shutil.copy2(db_path, tmp)
            try:
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()
                cur.execute("SELECT name, value FROM cookies WHERE host_key LIKE ?", (f'%{domain}%',))
                cookies = [f"{name}={value}" for name, value in cur.fetchall()]
                conn.close()
                os.remove(tmp)
                if cookies:
                    return '; '.join(cookies)
            except Exception as e:
                try: os.remove(tmp)
                except: pass
                continue
    return ''


def get_firefox_cookies(domain):
    """Try to extract cookies for domain from Firefox profile (Linux)."""
    home = os.path.expanduser('~')
    prof_glob = os.path.join(home, '.mozilla', 'firefox', '*.default*')
    for prof in glob.glob(prof_glob):
        db_path = os.path.join(prof, 'cookies.sqlite')
        if os.path.exists(db_path):
            tmp = db_path + '.tmp'
            shutil.copy2(db_path, tmp)
            try:
                conn = sqlite3.connect(tmp)
                cur = conn.cursor()
                cur.execute("SELECT name, value FROM moz_cookies WHERE host LIKE ?", (f'%{domain}%',))
                cookies = [f"{name}={value}" for name, value in cur.fetchall()]
                conn.close()
                os.remove(tmp)
                if cookies:
                    return '; '.join(cookies)
            except Exception as e:
                try: os.remove(tmp)
                except: pass
                continue
    return ''


def build_cookie_from_string(s):
    """Normalize a raw cookie string or return empty string if none."""
    if not s:
        return ''
    # if user passed 'name=value; name2=value2' already, use as-is
    return s.strip()


def search_ku_library(query, cookies):
    """Search KU library for query using cookies, return list of (title, url) tuples."""
    # Use Khalifa Primo URL
    search_url = 'https://khalifa.primo.exlibrisgroup.com/primo-explore/search'
    params = {'query': query, 'tab': 'default_tab', 'search_scope': 'MyInstitution', 'vid': 'KUAE'}
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
    }
    if cookies:
        headers['Cookie'] = cookies

    try:
        resp = requests.get(search_url, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        # Parse HTML for results. Look for PDF links or full text links.
        results = []
        # Simple regex for links to PDFs or full text
        links = re.findall(r'<a[^>]*href="([^"]*(?:pdf|fulltext|download)[^"]*)"[^>]*>([^<]*)</a>', resp.text, re.IGNORECASE)
        for url, title in links[:10]:
            if url.startswith('/'):
                url = 'https://library.ku.ac.ae' + url
            results.append((title.strip(), url))
        return results
    except Exception as e:
        print('KU library search failed:', e)
        return []

def main():

    parser = argparse.ArgumentParser(description='Fetch literature (English keywords) and download PDFs.')
    parser.add_argument('--cookies-file', help='Path to Netscape cookies.txt (for authenticated downloads)')
    parser.add_argument('--cookie-string', help='Raw Cookie header string to use for downloads')
    parser.add_argument('--auto-cookies', action='store_true', help='Try to auto-extract cookies from Chrome/Firefox for ku.ac.ae')
    parser.add_argument('--max', type=int, default=200, help='Maximum number of records to process')
    args = parser.parse_args()

    ku_cookie_header = ''
    if args.auto_cookies:
        # Try Chrome first, then Firefox
        ku_cookie_header = get_chrome_cookies('ku.ac.ae')
        if not ku_cookie_header:
            ku_cookie_header = get_firefox_cookies('ku.ac.ae')
        if ku_cookie_header:
            print('[INFO] Auto-extracted cookies for ku.ac.ae from browser profile.')
        else:
            print('[WARN] Could not auto-extract cookies for ku.ac.ae. Try logging in with your browser first.')
            print('To export cookies manually:')
            print('  - In Firefox: Install "Export Cookies" extension, export cookies.txt for ku.ac.ae')
            print('  - Then run: python3 fetch_literature_en.py --cookies-file /path/to/cookies.txt')
            print('  - Or copy cookie string from browser dev tools and use --cookie-string')
    if args.cookies_file:
        ku_cookie_header = load_cookies_netscape(args.cookies_file, domain_filter='ku.ac.ae') or ku_cookie_header
    if args.cookie_string:
        ku_cookie_header = build_cookie_from_string(args.cookie_string) or ku_cookie_header

    kws = extract_english_keywords()
    if not kws:
        print('No English keywords found in', KW_FILE)
        sys.exit(1)

    query = ' '.join(kws[:12])
    print('English query:', query)

    # Search KU library first
    ku_results = []
    if ku_cookie_header:
        print('Searching KU library...')
        ku_results = search_ku_library(query, ku_cookie_header)
        print(f'Found {len(ku_results)} results from KU library')
        for title, url in ku_results:
            print(f'  {title[:60]}... -> {url}')

    # Then CrossRef
    resp = query_crossref(query, rows=args.max)
    if not resp or 'message' not in resp:
        print('No results from CrossRef')
        sys.exit(1)

    items = resp['message'].get('items', [])
    print('Found', len(items), 'items from CrossRef')

    # Process KU results first
    count = 0
    for title, url in ku_results:
        filename_base = sanitize_filename(f"ku_{title}")[:200]
        pdf_name = filename_base + '.pdf'
        pdf_path = os.path.join(REF_DIR, pdf_name)

        append_urls(url, pdf_name)
        append_metadata([pdf_name, title, '', '', 'KU Library', '', url, '', 'en'])
        append_summary([pdf_name, '', '', '', 'From KU library search'])

        if not os.path.exists(pdf_path):
            print('Downloading from KU:', title[:80])
            ok = download_file(url, pdf_path, cookie_header=ku_cookie_header)
            if ok:
                print('Saved to', pdf_path)
            else:
                print('Failed to download', url)
        count += 1

    # Then process CrossRef items
    for it in items:
        doi = it.get('DOI', '')
        title = ' '.join(it.get('title', [])) if it.get('title') else ''
        authors = []
        for a in it.get('author', [])[:6]:
            name = ' '.join(filter(None, [a.get('given',''), a.get('family','')])).strip()
            if name:
                authors.append(name)
        authors_s = '; '.join(authors)
        year = ''
        try:
            year = it.get('issued', {}).get('date-parts', [[None]])[0][0] or ''
        except Exception:
            year = ''
        journal = it.get('container-title', [''])[0]
        abstract = it.get('abstract', '')

        pdf_url = None
        for l in it.get('link', []) or []:
            url = l.get('URL')
            if not url:
                continue
            content_type = l.get('content-type','')
            if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
                pdf_url = url
                break

        url_field = it.get('URL', '')

        last_author = authors[0].split()[-1] if authors else 'anon'
        short = re.sub(r'\W+', '_', title)[:50]
        filename_base = f"{year}_{last_author}_{short}" if year else f"{last_author}_{short}"
        filename_base = sanitize_filename(filename_base)
        pdf_name = filename_base + '.pdf'
        pdf_path = os.path.join(REF_DIR, pdf_name)

        preferred_url = pdf_url or url_field or ''
        if preferred_url:
            append_urls(preferred_url, pdf_name)

        append_metadata([pdf_name, title, authors_s, year, journal, doi, preferred_url, abstract, 'en'])
        append_summary([pdf_name, '', '', '', ''])

        if pdf_url:
            if not os.path.exists(pdf_path):
                print('Downloading PDF:', title[:80])
                ok = download_file(pdf_url, pdf_path)
                if not ok and ku_cookie_header and 'ku.ac.ae' in pdf_url:
                    print('Retrying with KU cookies...')
                    ok = download_file(pdf_url, pdf_path, cookie_header=ku_cookie_header)
                if ok:
                    print('Saved to', pdf_path)
                else:
                    print('Failed to download', pdf_url)
        count += 1
        if count >= args.max + len(ku_results):
            break
        time.sleep(0.8)

    print('English search done. Check', URLS_FILE, 'and', METADATA_CSV)

if __name__ == '__main__':
    main()
