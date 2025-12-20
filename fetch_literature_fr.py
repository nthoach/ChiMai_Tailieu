#!/usr/bin/env python3
"""Fetch literature using the French keyword section from keywords.md and download PDFs.

Writes/updates: urls.txt, metadata.csv, summary.csv and downloads PDFs into References/.
"""
import argparse
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

def extract_french_keywords():
    lines = []
    in_fr = False
    with open(KW_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            s = line.strip()
            if s.lower().startswith('## français'):
                in_fr = True
                continue
            if in_fr:
                if s.startswith('## ') or s.startswith('---') or (s == '' and lines):  # Break on next section or separator
                    break
                if not s or s.startswith('#'):
                    continue
                if s.startswith('-'):
                    s = s[1:].strip()
                lines.append(s)
    return lines

def sanitize_filename(s):
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'[^\w\-_\.]', '', s)
    return s

def query_crossref(keywords, max_results=250):
    """Query CrossRef API with pagination."""
    base_url = 'https://api.crossref.org/works'
    all_items = []
    offset = 0
    rows = 50  # items per page

    while len(all_items) < max_results:
        query = ' '.join(keywords)
        params = {
            'query': query,
            'rows': min(rows, max_results - len(all_items)),
            'offset': offset,
            'sort': 'relevance',
            'order': 'desc'
        }

        url = f"{base_url}?{parse.urlencode(params)}"
        print(f"Querying CrossRef: {query} (offset: {offset})")

        try:
            with request.urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
                items = data.get('message', {}).get('items', [])
                all_items.extend(items)

                if len(items) < rows:
                    break  # no more results

                offset += rows
                time.sleep(1)  # rate limiting

        except error.HTTPError as e:
            print(f"HTTP Error: {e.code} - {e.reason}")
            break
        except Exception as e:
            print(f"Error querying CrossRef: {e}")
            break

    return all_items[:max_results]

def query_libgen(keywords):
    """Query LibGen for books/papers."""
    query = '+'.join(keywords)
    url = f"https://libgen.is/search.php?req={query}&lg_topic=libgen&open=0&view=simple&res=10&phrase=1&column=def"

    try:
        with request.urlopen(url, timeout=30) as response:
            html = response.read().decode('utf-8')
            # Simple regex to extract DOIs or titles (simplified)
            items = []
            # This is a placeholder - LibGen scraping is complex
            return items
    except Exception as e:
        print(f"LibGen query error: {e}")
        return []

def query_scihub(dois):
    """Query Sci-Hub for PDFs using DOIs."""
    pdf_urls = []
    for doi in dois:
        try:
            # Sci-Hub URL construction (may vary)
            scihub_url = f"https://sci-hub.se/{doi}"
            with request.urlopen(scihub_url, timeout=30) as response:
                html = response.read().decode('utf-8')
                # Extract PDF URL (simplified regex)
                match = re.search(r'href="([^"]*\.pdf[^"]*)"', html)
                if match:
                    pdf_urls.append(match.group(1))
        except Exception as e:
            print(f"Sci-Hub error for {doi}: {e}")
    return pdf_urls

def download_pdf(url, filename):
    """Download PDF from URL."""
    try:
        with request.urlopen(url, timeout=30) as response:
            if response.headers.get('content-type', '').startswith('application/pdf'):
                filepath = os.path.join(REF_DIR, filename)
                with open(filepath, 'wb') as f:
                    f.write(response.read())
                print(f"Saved to {filepath}")
                return True
            else:
                print(f"Not a PDF: {url}")
    except Exception as e:
        print(f"Failed to download {url}: {e}")
    return False

def process_items(items, urls_file, metadata_csv, summary_csv):
    """Process CrossRef items and update files."""
    new_urls = []
    new_metadata = []
    new_summary = []

    with open(urls_file, 'a', encoding='utf-8') as uf, \
         open(metadata_csv, 'a', newline='', encoding='utf-8') as mf, \
         open(summary_csv, 'a', newline='', encoding='utf-8') as sf:

        url_writer = csv.writer(uf)
        meta_writer = csv.writer(mf)
        sum_writer = csv.writer(sf)

        for item in items:
            title = item.get('title', [''])[0] if item.get('title') else ''
            doi = item.get('DOI', '')
            url = f"https://doi.org/{doi}" if doi else ''
            authors = '; '.join([f"{a.get('given', '')} {a.get('family', '')}".strip()
                               for a in item.get('author', [])])
            year = item.get('published-print', {}).get('date-parts', [[None]])[0][0] or \
                   item.get('published-online', {}).get('date-parts', [[None]])[0][0] or ''
            journal = item.get('container-title', [''])[0] if item.get('container-title') else ''

            if url and url not in [line.strip() for line in open(urls_file, 'r').readlines()]:
                url_writer.writerow([url])
                new_urls.append(url)

            meta_writer.writerow([title, authors, year, journal, doi, url])
            new_metadata.append([title, authors, year, journal, doi, url])

            sum_writer.writerow([title, url])
            new_summary.append([title, url])

            # Try to download PDF
            if doi:
                filename = sanitize_filename(f"{year}_{authors.split(';')[0] if authors else 'Unknown'}_{title[:50]}.pdf")
                pdf_url = f"https://www.sciencedirect.com/science/article/pii/{doi}" if 'sciencedirect' in url else \
                          f"https://journals.asm.org/doi/pdf/10.1128/{doi.split('/')[-1]}" if 'asm.org' in url else \
                          f"https://academic.oup.com/{'/'.join(doi.split('/')[:-1])}/article-pdf/{doi.split('/')[-1]}/{doi.split('/')[-1]}.pdf" if 'oup.com' in url else None
                if pdf_url:
                    download_pdf(pdf_url, filename)

    return len(new_urls), len(new_metadata)

def main():
    parser = argparse.ArgumentParser(description='Fetch French literature on mycotoxins in herbal medicine')
    parser.add_argument('--max', type=int, default=250, help='Maximum results to fetch')
    args = parser.parse_args()

    keywords = extract_french_keywords()
    if not keywords:
        print("No French keywords found in keywords.md")
        return

    print(f"French keywords: {keywords}")

    # Query sources
    crossref_items = query_crossref(keywords, args.max)
    print(f"Found {len(crossref_items)} items from CrossRef")

    libgen_items = query_libgen(keywords)
    print(f"Found {len(libgen_items)} items from LibGen")

    # Extract DOIs for Sci-Hub
    dois = [item.get('DOI', '') for item in crossref_items if item.get('DOI')]
    scihub_pdfs = query_scihub(dois)
    print(f"Found {len(scihub_pdfs)} PDFs from Sci-Hub")

    # Process and save
    new_urls, new_meta = process_items(crossref_items, URLS_FILE, METADATA_CSV, SUMMARY_CSV)

    print(f"French search done. Check {URLS_FILE} and {METADATA_CSV}")

if __name__ == '__main__':
    main()