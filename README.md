# Cybersecurity Conference Scraper

**Dynamic scraper** – no hardcoded data. Discovers conferences from live sources (Infosec-Conferences, sec-deadlines, Cryptax/confsec, WikiCFP, BSides.org). Runs daily to pick up new events for **2025–2027**.

## Layout

```
scraper/
├── scraper.py          # Scrape conferences (run first)
├── app.py             # Web UI server
├── templates/
│   └── index.html     # Conference list UI
├── conferences.db     # SQLite database (auto-created by scraper)
├── requirements.txt   # Dependencies
└── README.md          # This file
```

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Scrape conferences (run first)

```bash
python3 scraper.py
```

### Web UI

```bash
python3 app.py
```

Then open **http://127.0.0.1:5000** in your browser. Filter by region (Asia, Europe, Americas, etc.), year (2025–2027), and search by name or location.

### Deploy on Render

The app needs **conferences.db** to be created by running the scraper. On Render, the **Start Command** must run the scraper first, then start the web server:

```bash
python3 scraper.py && gunicorn app:app --bind 0.0.0.0:$PORT
```

- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** (above) — or use the repo’s **Procfile** (Render uses it if present).

The first deploy may take a few minutes while the scraper runs (many sources, rate limiting). After that, the UI will show data. The database lives in the service’s filesystem; it is repopulated on each deploy or service restart.

### Schedule daily (Linux/macOS)

```bash
crontab -e
# Add (runs every day at 9 AM):
0 9 * * * cd /path/to/scraper && /usr/bin/python3 scraper.py >> scraper.log 2>&1
```

### Schedule daily (Windows Task Scheduler)

1. Open Task Scheduler → Create Basic Task.
2. Trigger: Daily at 9:00 AM.
3. Action: Start a program  
   - Program: `python.exe`  
   - Arguments: `C:\path\to\scraper\scraper.py`  
   - Start in: `C:\path\to\scraper`

## Output

- **Database**: `conferences.db` (SQLite) — conferences table (name, location, region, start_date, cfp_deadline, website, source, discovered_at, last_seen) and `scrape_log` for each run.
- **Console**: Summary by region, then Asia and Europe conference lists grouped by month.

## Sources (in scraper.py)

- **Infosec-Conferences.com** – conference list page
- **sec-deadlines** – GitHub YAML (academic CFPs)
- **Cryptax/confsec** – GitHub markdown table
- **WikiCFP** – search by security/cybersecurity/infosec/hacking
- **BSides.org** – official BSides listing

Add more scrapers in `scraper.py` and register them in the `scrapers` list in `scrape_all()`.

## Query the database

```bash
# All upcoming in Asia
sqlite3 conferences.db "SELECT name, start_date, location FROM conferences WHERE region='Asia' AND start_date >= date('now') ORDER BY start_date;"

# By source
sqlite3 conferences.db "SELECT name, start_date, source FROM conferences WHERE source LIKE '%sec-deadlines%' ORDER BY start_date;"
```

## Notes

- **No hardcoded conferences** – all data comes from live scrapes; re-run the scraper to discover new events.
- **Scrape log** – each run is logged in `scrape_log` (source, count, status, error_msg).
- **Updates** – existing conferences (same name + start_date) get `last_seen` updated; new ones get `discovered_at`.
- Scrapers are best-effort; site HTML changes may require selector updates in `scraper.py`.
