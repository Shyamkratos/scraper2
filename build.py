#!/usr/bin/env python3
"""
Vercel build script: populate conferences.db at deploy time so the UI has data.
Invoke with: uv run python build.py  (so deps from pyproject.toml are available)
"""
import os
import sys

# Run from project root so scraper finds sources.json and writes conferences.db there
os.chdir(os.path.dirname(os.path.abspath(__file__)))

def main():
    from scraper import init_database, scrape_all
    print("Running scraper for Vercel build...", flush=True)
    init_database()
    scrape_all()
    db_path = os.environ.get("CONFERENCES_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "conferences.db"))
    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        print(f"Created {db_path} ({size} bytes)", flush=True)
    else:
        print("Warning: conferences.db was not created", flush=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
