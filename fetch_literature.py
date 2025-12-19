#!/usr/bin/env python3
"""Fetch literature metadata from CrossRef and download available PDFs.

Usage: python3 fetch_literature.py

Outputs:
- updates `urls.txt`, `metadata.csv`, `summary.csv`
- downloads PDFs into `References/`
"""
import csv
import json
import os
import re
import sys
import time
from urllib import parse, request, error

BASE_DIR = os.path.dirname(__file__)
KEYWORDS_FILE = os.path.join(BASE_DIR, 'keywords.md')
URLS_FILE = os.path.join(BASE_DIR, 'urls.txt')
METADATA_CSV = os.path.join(BASE_DIR, 'metadata.csv')
SUMMARY_CSV = os.path.join(BASE_DIR, 'summary.csv')
REF_DIR = os.path.join(BASE_DIR, 'References')

os.makedirs(REF_DIR, exist_ok=True)

def read_keywords():
    kws = []
    with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
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

def query_crossref(query, rows=20):
    q = parse.quote(query)
    url = f'https://api.crossref.org/works?query={q}&rows={rows}'
    try:
        with request.urlopen(url, timeout=30) as resp:
            data = resp.read()
            return json.loads(data.decode('utf-8'))
    except error.URLError as e:
        print('Network error querying CrossRef:', e)
        return None

def download_file(url, outpath):
    try:
        req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with request.urlopen(req, timeout=60) as resp:
            with open(outpath, 'wb') as f:
                f.write(resp.read())
        return True
    except Exception as e:
        print('Download failed for', url, ':', e)
        return False

def main():
    kws = read_keywords()
    if not kws:
        print('No keywords found in', KEYWORDS_FILE)
        sys.exit(1)

    # build a compact query using first 6 keywords
    query = ' '.join(kws[:8])
    print('Querying CrossRef for:', query)
    resp = query_crossref(query, rows=50)
    if not resp or 'message' not in resp:
        print('No response from CrossRef')
        sys.exit(1)

    items = resp['message'].get('items', [])
    print(f'Found {len(items)} items')
    count = 0
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

        # prefer link entries that point to PDFs
        pdf_url = None
        for l in it.get('link', []) or []:
            url = l.get('URL')
            if not url:
                continue
            content_type = l.get('content-type','')
            if 'pdf' in content_type.lower() or url.lower().endswith('.pdf'):
                pdf_url = url
                break

        # fallback: use URL or 'URL' field
        url_field = it.get('URL', '')

        # build filename
        last_author = authors[0].split()[-1] if authors else 'anon'
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

        if pdf_url:
            if not os.path.exists(pdf_path):
                print('Downloading PDF for:', title[:80])
                ok = download_file(pdf_url, pdf_path)
                if ok:
                    print('Saved to', pdf_path)
                else:
                    print('Failed to download', pdf_url)
            else:
                print('Already downloaded', pdf_name)

        count += 1
        if count >= 50:
            break
        time.sleep(1)

    print('Done. Metadata and URLs updated. Check', URLS_FILE, 'and', METADATA_CSV)

if __name__ == '__main__':
    main()
