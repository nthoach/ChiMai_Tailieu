ChiMai_Tailieu — Literature collection for mycotoxins in herbal medicine

Files:
- keywords.md — multilingual search keywords
- request.txt — task description and steps
- urls.txt — collected DOIs/URLs
- References/ — folder to store downloaded PDFs
- metadata.csv — extracted metadata for each PDF
- summary.csv — structured summaries
- run_search.sh — helper script to open Google Scholar

How to use:
1. Review `keywords.md` and adjust keywords.
2. Run `./run_search.sh` to open Google Scholar with suggested query.
3. Save DOIs/URLs in `urls.txt` and download PDFs into `References/`.
4. Fill `metadata.csv` and `summary.csv`.

Requirements:
- Internet access and library credentials for paywalled content.
- `xdg-open`, `python3`, and a modern browser.
