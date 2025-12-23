#!/usr/bin/env python3
"""Fetch literature metadata from CrossRef, LibGen, and Sci-Hub, and download available PDFs.

Usage: python3 fetch_literature.py

Outputs:
- updates `urls.txt`, `metadata.csv`, `summary.csv`
- downloads PDFs into `References/`

Sources:
- CrossRef API (primary academic database)
- LibGen (open-access book/paper repository)
- Sci-Hub (PDF access for DOIs)

Note: LibGen and Sci-Hub access may be unreliable due to network restrictions.
"""
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
KEYWORDS_FILE = os.path.join(BASE_DIR, 'keywords.md')
URLS_FILE = os.path.join(BASE_DIR, 'urls.txt')
METADATA_CSV = os.path.join(BASE_DIR, 'metadata.csv')
SUMMARY_CSV = os.path.join(BASE_DIR, 'summary.csv')
REF_DIR = os.path.join(BASE_DIR, 'References')

os.makedirs(REF_DIR, exist_ok=True)

def read_keywords():
    kws = []
    in_vietnamese = False
    with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line == '## Tiếng Việt':
                in_vietnamese = True
                continue
            elif line.startswith('##') and in_vietnamese:
                break  # Stop at next section
            if in_vietnamese:
                if not line or line.startswith('#'):
                    continue
                # skip section headers
                if line.endswith(':'):
                    continue
                # if line starts with '-', remove it
                if line.startswith('-'):
                    line = line[1:].strip()
                kws.append(line)
    return kws

def sanitize_filename(s):
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^0-9A-Za-z_\-\.]+', '', s)
    return s[:200]

def append_urls(url, preferred):
    with open(URLS_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{url}\t{preferred}\n")

def append_metadata(row):
    exists = os.path.exists(METADATA_CSV)
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

def query_crossref(query, rows=20, offset=0):
    q = parse.quote(query)
    url = f'https://api.crossref.org/works?query={q}&rows={rows}&offset={offset}'
    try:
        with request.urlopen(url, timeout=30) as resp:
            data = resp.read()
            return json.loads(data.decode('utf-8'))
    except error.URLError as e:
        print('Network error querying CrossRef:', e)
        return None

def download_file(url, outpath, timeout=60):
    try:
        req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with request.urlopen(req, timeout=timeout) as resp:
            with open(outpath, 'wb') as f:
                f.write(resp.read())
        return True
    except Exception as e:
        print('Download failed for', url, ':', e)
        return False


def query_libgen(query, rows=20):
    """Query LibGen mirrors for the query and return list of items with keys: title, authors, year, doi, url, pdf_url."""
    mirrors = [
        'https://libgen.is/search.php',
        'https://libgen.rs/search.php',
        'https://libgen.st/search.php',
        'https://libgen.li/search.php',
        'https://libgen.lc/search.php',
        'https://libgen.rocks/search.php',
        'https://libgen.fun/search.php',
        'https://libgen.me/search.php',
        'https://libgen.org/search.php',
        'https://libgen.io/search.php'
    ]
    for base in mirrors:
        try:
            params = {'req': query, 'lg_topic': 'libgen', 'open': '0', 'view': 'simple', 'res': rows, 'phrase': '1', 'column': 'def'}
            resp = requests.get(base, params=params, timeout=20)
            resp.raise_for_status()
            html = resp.text
            items = []
            # find table rows
            rows_html = re.findall(r'<tr.*?>(.*?)</tr>', html, re.DOTALL)
            # Skip header row and parse results
            for row in rows_html[1:rows+1]:
                cols = re.findall(r'<td.*?>(.*?)</td>', row, re.DOTALL)
                if len(cols) < 5:
                    continue
                authors = re.sub(r'<[^>]+>', '', cols[1]).strip()
                title = re.sub(r'<[^>]+>', '', cols[2]).strip()
                year = re.sub(r'<[^>]+>', '', cols[4]).strip()
                # download link (may be relative)
                mirrors_found = re.findall(r'href="([^"]*download[^"]*)"', row)
                pdf_url = ''
                if mirrors_found:
                    pdf_url = mirrors_found[0]
                    if pdf_url.startswith('/'):
                        pdf_url = base.replace('/search.php', '') + pdf_url
                doi = ''
                items.append({'title': title, 'authors': authors, 'year': year, 'doi': doi, 'url': pdf_url, 'pdf_url': pdf_url, 'journal': '', 'abstract': ''})
            if items:
                return items
        except Exception as e:
            # try next mirror
            continue
    return []


def query_scihub(dois, timeout=20, max_per_run=10):
    """Query Sci-Hub mirrors for given DOIs and return list of {'doi', 'pdf_url'} matches."""
    scihub_bases = [
        'https://sci-hub.se/',
        'https://sci-hub.ru/',
        'https://sci-hub.st/',
        'https://sci-hub.tw/',
        'https://sci-hub.hk/',
        'https://sci-hub.mn/',
        'https://sci-hub.ee/',
        'https://sci-hub.do/',
        'https://sci-hub.pl/'
    ]
    found = []
    for doi in (dois or [])[:max_per_run]:
        if not doi:
            continue
        for base in scihub_bases:
            try:
                url = base + doi
                resp = requests.get(url, timeout=timeout, headers={'User-Agent':'Mozilla/5.0'})
                if resp.status_code != 200:
                    continue
                # Try to find pdf src or iframe
                m = re.search(r'src="([^"]*\.pdf[^"]*)"', resp.text)
                if not m:
                    m = re.search(r'href="([^"]*\.pdf[^"]*)"', resp.text)
                if m:
                    pdf_url = m.group(1)
                    if pdf_url.startswith('//'):
                        pdf_url = 'https:' + pdf_url
                    elif pdf_url.startswith('/'):
                        pdf_url = base.rstrip('/') + pdf_url
                    found.append({'doi': doi, 'pdf_url': pdf_url})
                    break
            except Exception:
                continue
    return found

def main():
    kws = read_keywords()
    if not kws:
        print('No keywords found in', KEYWORDS_FILE)
        sys.exit(1)

    # build a compact query using all Vietnamese keywords
    query = ' '.join(kws)
    print('Querying CrossRef for:', query)
    
    # Search CrossRef with pagination
    all_crossref_items = []
    offset = 0
    max_pages = 5  # Fetch up to 5 pages of 50 items each
    for page in range(max_pages):
        resp = query_crossref(query, rows=50, offset=offset)
        if not resp or 'message' not in resp:
            break
        items = resp['message'].get('items', [])
        if not items:
            break
        all_crossref_items.extend(items)
        offset += 50
        if len(items) < 50:  # Last page
            break
        time.sleep(2)  # Rate limiting
    
    print(f'Found {len(all_crossref_items)} items from CrossRef')

    # Also try LibGen (open-access mirrors)
    try:
        libgen_items = query_libgen(query, rows=20)
        print(f'Found {len(libgen_items)} items from LibGen')
    except Exception as e:
        libgen_items = []
        print('LibGen search failed:', e)

    # Collect DOIs for Sci-Hub
    dois = [it.get('DOI') for it in all_crossref_items if it.get('DOI')] + [it.get('doi') for it in libgen_items if it.get('doi')]
    scihub_items = []
    if dois:
        try:
            scihub_items = query_scihub(dois)
            print(f'Found {len(scihub_items)} PDFs from Sci-Hub')
        except Exception as e:
            print('Sci-Hub search failed:', e)

    count = 0
    for it in (all_crossref_items + libgen_items):
        # Handle different item formats
        if 'DOI' in it:  # CrossRef item
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
            url_field = it.get('URL', '')
            pdf_url = None
            for l in it.get('link', []) or []:
                url = l.get('URL')
                if not url:
                    continue
                content_type = l.get('content-type','')
                if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
                    pdf_url = url
                    break
        else:  # LibGen item
            doi = it.get('doi', '')
            title = it.get('title', '')
            authors_s = it.get('authors', '')
            year = it.get('year', '')
            journal = it.get('journal', '')
            abstract = it.get('abstract', '')
            url_field = it.get('url', '')
            pdf_url = it.get('pdf_url', '')

        # build filename
        last_author = authors_s.split(';')[0].strip().split()[-1] if authors_s else 'anon'
        short = re.sub(r'\W+', '_', title)[:50]
        filename_base = f"{year}_{last_author}_{short}" if year else f"{last_author}_{short}"
        filename_base = sanitize_filename(filename_base)
        pdf_name = filename_base + '.pdf'
        pdf_path = os.path.join(REF_DIR, pdf_name)

        # append to urls and metadata
        preferred_url = pdf_url or url_field or ''
        if preferred_url:
            append_urls(preferred_url, pdf_name)

        append_metadata([pdf_name, title, authors_s, year, journal, doi, preferred_url, abstract, ''])
        append_summary([pdf_name, '', '', '', ''])

        # Try to download PDF
        downloaded = False
        if pdf_url:
            if not os.path.exists(pdf_path):
                print('Downloading PDF for:', title[:80])
                ok = download_file(pdf_url, pdf_path)
                if ok:
                    print('Saved to', pdf_path)
                    downloaded = True
                else:
                    print('Failed to download', pdf_url)
            else:
                print('Already downloaded', pdf_name)
                downloaded = True

        # If no PDF yet, try Sci-Hub
        if not downloaded and doi:
            scihub_pdf = next((s['pdf_url'] for s in scihub_items if s['doi'] == doi), None)
            if scihub_pdf and not os.path.exists(pdf_path):
                print('Trying Sci-Hub for:', title[:80])
                ok = download_file(scihub_pdf, pdf_path)
                if ok:
                    print('Saved from Sci-Hub to', pdf_path)
                else:
                    print('Failed Sci-Hub download', scihub_pdf)

        count += 1
        if count >= 250:
            break
        time.sleep(1)

    print('Done. Metadata and URLs updated. Check', URLS_FILE, 'and', METADATA_CSV)

if __name__ == '__main__':
    main()
