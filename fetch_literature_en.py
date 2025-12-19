#!/usr/bin/env python3
"""Fetch literature using the English keyword section from keywords.md and download PDFs.

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


def query_pubmed(query, rows=50):
    """Query PubMed for papers."""
    import xml.etree.ElementTree as ET
    base_url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
    params = {'db': 'pubmed', 'term': query, 'retmax': rows, 'retmode': 'xml'}
    try:
        resp = requests.get(base_url, params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        ids = [id_elem.text for id_elem in root.findall('.//Id')]
        # Fetch summaries
        if ids:
            summary_url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
            summary_params = {'db': 'pubmed', 'id': ','.join(ids[:rows]), 'retmode': 'xml'}
            summary_resp = requests.get(summary_url, params=summary_params)
            summary_resp.raise_for_status()
            summary_root = ET.fromstring(summary_resp.text)
            items = []
            for doc in summary_root.findall('.//DocSum'):
                title = doc.find('.//Item[@Name="Title"]').text if doc.find('.//Item[@Name="Title"]') is not None else ''
                authors = []
                for author in doc.findall('.//Item[@Name="Author"]'):
                    authors.append(author.text)
                authors_s = '; '.join(authors)
                year = doc.find('.//Item[@Name="PubDate"]').text[:4] if doc.find('.//Item[@Name="PubDate"]') else ''
                journal = doc.find('.//Item[@Name="Source"]').text if doc.find('.//Item[@Name="Source"]') else ''
                doi = ''
                for id_elem in doc.findall('.//Item[@Name="DOI"]'):
                    doi = id_elem.text
                    break
                url = f'https://pubmed.ncbi.nlm.nih.gov/{doc.find(".//Id").text}/' if doc.find('.//Id') else ''
                abstract = ''  # PubMed summary doesn't include abstract
                items.append({'title': title, 'authors': authors_s, 'year': year, 'journal': journal, 'doi': doi, 'url': url, 'abstract': abstract})
            return items
    except Exception as e:
        print('PubMed query error:', e)
        return []

def download_file(url, outpath, timeout=60):
    """Download a URL to outpath."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/117.0.0.0 Safari/537.36'
    }
    try:
        req = request.Request(url, headers=headers)
        with request.urlopen(req, timeout=timeout) as resp:
            with open(outpath, 'wb') as f:
                f.write(resp.read())
        return True
    except Exception as e:
        print('Download failed for', url, ':', e)
        return False


def query_libgen(query, rows=20):
    """Query LibGen for papers."""
    # LibGen search URL - try alternative mirrors
    search_urls = [
        'https://libgen.is/search.php',
        'https://libgen.rs/search.php',
        'https://libgen.st/search.php'
    ]
    
    for search_url in search_urls:
        try:
            params = {'req': query, 'lg_topic': 'libgen', 'open': '0', 'view': 'simple', 'res': rows, 'phrase': '1', 'column': 'def'}
            resp = requests.get(search_url, params=params, timeout=30)
            resp.raise_for_status()
            # Parse HTML for results
            items = []
            # Look for table rows with paper info
            rows_html = re.findall(r'<tr.*?>(.*?)</tr>', resp.text, re.DOTALL)
            for row in rows_html[1:rows+1]:  # Skip header
                cols = re.findall(r'<td.*?>(.*?)</td>', row, re.DOTALL)
                if len(cols) >= 9:
                    title = re.sub(r'<[^>]+>', '', cols[2]).strip()
                    authors = re.sub(r'<[^>]+>', '', cols[1]).strip()
                    year = re.sub(r'<[^>]+>', '', cols[4]).strip()
                    # Look for download links
                    mirrors = re.findall(r'href="([^"]*download[^"]*)"', row)
                    pdf_url = mirrors[0] if mirrors else ''
                    if pdf_url.startswith('/'):
                        base_url = search_url.replace('/search.php', '')
                        pdf_url = base_url + pdf_url
                    doi = ''  # LibGen doesn't always have DOI
                    url = pdf_url
                    items.append({'title': title, 'authors': authors, 'year': year, 'journal': '', 'doi': doi, 'url': url, 'pdf_url': pdf_url, 'abstract': ''})
            return items
        except Exception as e:
            print(f'LibGen query error with {search_url}:', e)
            continue
    return []
    """Query PubMed for papers."""
    import xml.etree.ElementTree as ET
    base_url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi'
    params = {'db': 'pubmed', 'term': query, 'retmax': rows, 'retmode': 'xml'}
    try:
        resp = requests.get(base_url, params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        ids = [id_elem.text for id_elem in root.findall('.//Id')]
        # Fetch summaries
        if ids:
            summary_url = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi'
            summary_params = {'db': 'pubmed', 'id': ','.join(ids[:rows]), 'retmode': 'xml'}
            summary_resp = requests.get(summary_url, params=summary_params)
            summary_resp.raise_for_status()
            summary_root = ET.fromstring(summary_resp.text)
            items = []
            for doc in summary_root.findall('.//DocSum'):
                title = doc.find('.//Item[@Name="Title"]').text if doc.find('.//Item[@Name="Title"]') is not None else ''
                authors = []
                for author in doc.findall('.//Item[@Name="Author"]'):
                    authors.append(author.text)
                authors_s = '; '.join(authors)
                year = doc.find('.//Item[@Name="PubDate"]').text[:4] if doc.find('.//Item[@Name="PubDate"]') else ''
                journal = doc.find('.//Item[@Name="Source"]').text if doc.find('.//Item[@Name="Source"]') else ''
                doi = ''
                for id_elem in doc.findall('.//Item[@Name="DOI"]'):
                    doi = id_elem.text
                    break
                url = f'https://pubmed.ncbi.nlm.nih.gov/{doc.find(".//Id").text}/' if doc.find('.//Id') else ''
                abstract = ''  # PubMed summary doesn't include abstract
                items.append({'title': title, 'authors': authors_s, 'year': year, 'journal': journal, 'doi': doi, 'url': url, 'abstract': abstract})
            return items
    except Exception as e:
        print('PubMed query error:', e)
        return []


def query_libgen(query, rows=20):
    """Query LibGen for papers."""
    # LibGen search URL
    search_url = 'https://libgen.is/search.php'
    params = {'req': query, 'lg_topic': 'libgen', 'open': '0', 'view': 'simple', 'res': rows, 'phrase': '1', 'column': 'def'}
    try:
        resp = requests.get(search_url, params=params, timeout=30)
        resp.raise_for_status()
        # Parse HTML for results
        items = []
        # Look for table rows with paper info
        rows_html = re.findall(r'<tr.*?>(.*?)</tr>', resp.text, re.DOTALL)
        for row in rows_html[1:rows+1]:  # Skip header
            cols = re.findall(r'<td.*?>(.*?)</td>', row, re.DOTALL)
            if len(cols) >= 9:
                title = re.sub(r'<[^>]+>', '', cols[2]).strip()
                authors = re.sub(r'<[^>]+>', '', cols[1]).strip()
                year = re.sub(r'<[^>]+>', '', cols[4]).strip()
                # Look for download links
                mirrors = re.findall(r'href="([^"]*download[^"]*)"', row)
                pdf_url = mirrors[0] if mirrors else ''
                if pdf_url.startswith('/'):
                    pdf_url = 'https://libgen.is' + pdf_url
                doi = ''  # LibGen doesn't always have DOI
                url = pdf_url
                items.append({'title': title, 'authors': authors, 'year': year, 'journal': '', 'doi': doi, 'url': url, 'pdf_url': pdf_url, 'abstract': ''})
        return items
    except Exception as e:
        print('LibGen query error:', e)
        return []


def query_scihub(dois, timeout=30):
    """Query Sci-Hub for papers by DOI."""
    items = []
    scihub_urls = ['https://sci-hub.se/', 'https://sci-hub.ru/', 'https://sci-hub.st/']
    
    for doi in dois[:10]:  # Limit to avoid too many requests
        if not doi:
            continue
        for base_url in scihub_urls:
            try:
                # Sci-Hub URL for DOI
                scihub_url = f'{base_url}{doi}'
                resp = requests.get(scihub_url, timeout=timeout, headers={
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36'
                })
                if resp.status_code == 200:
                    # Look for PDF download link
                    pdf_match = re.search(r'href="([^"]*\.pdf[^"]*)"', resp.text)
                    if pdf_match:
                        pdf_url = pdf_match.group(1)
                        if pdf_url.startswith('//'):
                            pdf_url = 'https:' + pdf_url
                        elif pdf_url.startswith('/'):
                            pdf_url = base_url.rstrip('/') + pdf_url
                        items.append({'doi': doi, 'pdf_url': pdf_url})
                        break  # Found it, move to next DOI
            except Exception as e:
                continue  # Try next mirror
    return items


def main():

    parser = argparse.ArgumentParser(description='Fetch literature (English keywords) and download PDFs from open access sources.')
    parser.add_argument('--max', type=int, default=200, help='Maximum number of records to process')
    args = parser.parse_args()

    kws = extract_english_keywords()
    if not kws:
        print('No English keywords found in', KW_FILE)
        sys.exit(1)

    query = ' '.join(kws[:12])
    print('English query:', query)

    # Search CrossRef
    resp = query_crossref(query, rows=args.max)
    crossref_items = resp['message'].get('items', []) if resp and 'message' in resp else []
    print('Found', len(crossref_items), 'items from CrossRef')

    # Also PubMed
    pubmed_items = query_pubmed(query, rows=args.max) or []
    print('Found', len(pubmed_items), 'items from PubMed')

    # Also LibGen
    libgen_items = []
    try:
        libgen_items = query_libgen(query, rows=min(args.max, 20)) or []
        print('Found', len(libgen_items), 'items from LibGen')
    except Exception as e:
        print('LibGen search failed:', e)

    items = crossref_items + pubmed_items + libgen_items  # Combine

    # Collect DOIs for Sci-Hub
    dois = []
    for it in crossref_items + pubmed_items:
        if 'DOI' in it and it.get('DOI'):
            dois.append(it['DOI'])
        elif isinstance(it, dict) and it.get('doi'):
            dois.append(it['doi'])
    
    # Search Sci-Hub for PDFs
    scihub_items = []
    if dois:
        try:
            scihub_items = query_scihub(dois[:min(args.max, 20)])  # Limit to avoid too many requests
            print('Found', len(scihub_items), 'PDFs from Sci-Hub')
        except Exception as e:
            print('Sci-Hub search failed:', e)

    # Process items
    count = 0
    for it in items:
        if 'DOI' in it:  # CrossRef
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
        elif 'pdf_url' in it:  # LibGen
            doi = it.get('doi', '')
            title = it.get('title', '')
            authors_s = it.get('authors', '')
            year = it.get('year', '')
            journal = it.get('journal', '')
            abstract = it.get('abstract', '')
            url_field = it.get('url', '')
            pdf_url = it.get('pdf_url')
        else:  # PubMed
            doi = it.get('doi', '')
            title = it.get('title', '')
            authors_s = it.get('authors', '')
            year = it.get('year', '')
            journal = it.get('journal', '')
            abstract = it.get('abstract', '')
            url_field = it.get('url', '')
            pdf_url = None  # PubMed doesn't provide PDF links directly

        last_author = authors_s.split(';')[0].strip().split()[-1] if authors_s else 'anon'
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
                if ok:
                    print('Saved to', pdf_path)
                else:
                    print('Failed to download', pdf_url)
        elif doi:
            # Try Sci-Hub for items with DOI but no direct PDF
            scihub_pdf = None
            for sci_item in scihub_items:
                if sci_item.get('doi') == doi:
                    scihub_pdf = sci_item.get('pdf_url')
                    break
            if scihub_pdf and not os.path.exists(pdf_path):
                print('Downloading from Sci-Hub:', title[:80])
                ok = download_file(scihub_pdf, pdf_path)
                if ok:
                    print('Saved to', pdf_path)
                else:
                    print('Failed to download from Sci-Hub', scihub_pdf)
        count += 1
        if count >= args.max:
            break
        time.sleep(0.8)

    print('English search done. Check', URLS_FILE, 'and', METADATA_CSV)

if __name__ == '__main__':
    main()
