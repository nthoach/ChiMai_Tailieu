#!/bin/bash
# Simple script to search Google Scholar using query from keywords.md (interactive)
# NOTE: This doesn't bypass Google protections; use manually if blocked.

QUERY=$(sed -n '1,40p' keywords.md | tr '\n' ' ' | sed 's/  */ /g')
ESCAPED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")
echo "Opening Google Scholar with query: $QUERY"
xdg-open "https://scholar.google.com/scholar?q=$ESCAPED"
