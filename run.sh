#!/usr/bin/env bash
# Run scraper to populate DB, then start the web server (for Render / production).
set -e
echo "Running scraper to populate conferences.db..."
python3 scraper.py
echo "Starting web server..."
exec gunicorn app:app --bind 0.0.0.0:${PORT:-5000}
