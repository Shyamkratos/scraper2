#!/usr/bin/env python3
"""
Simple web UI for Cybersecurity Conference Scraper.
Reads from conferences.db and serves a filterable list.
"""

import re
import sqlite3
import os
from datetime import datetime
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)
DATABASE = os.environ.get("CONFERENCES_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "conferences.db"))

# Display sanitization: fix raw markdown and :us: shortcodes in existing DB rows
EMOJI_SHORTCODES = {
    ":us:": "USA", ":usa:": "USA", ":uk:": "UK", ":gb:": "UK", ":de:": "Germany",
    ":fr:": "France", ":in:": "India", ":jp:": "Japan", ":ca:": "Canada", ":au:": "Australia",
}

def sanitize_name(s):
    if not s:
        return s
    s = str(s).strip()
    m = re.search(r"\[([^\]]+)\]\([^)]+\)", s)
    if m:
        s = m.group(1)
    m = re.search(r"\\?(.+?)\((https?://[^)]+)\)", s)
    if m:
        s = m.group(1).replace("\\", "").strip()
    s = re.sub(r"[\*`\[\]\\]", "", s).strip()
    s = re.sub(r"/+", "/", s).strip("/")
    return s or str(s)

def sanitize_location(s):
    if not s:
        return s
    s = str(s).strip()
    for code, country in EMOJI_SHORTCODES.items():
        s = s.replace(code, country)
    s = re.sub(r":[a-z_]+:", "", s, flags=re.I).strip()
    return re.sub(r"\s+,", ",", s).strip()


def get_db():
    if not os.path.exists(DATABASE):
        return None
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/conferences")
def api_conferences():
    region = request.args.get("region", "").strip() or None
    year = request.args.get("year", "").strip() or None
    search = request.args.get("q", "").strip() or None

    conn = get_db()
    if not conn:
        return jsonify({"conferences": [], "summary": {}})

    cursor = conn.cursor()

    # Schema: name, location, country, region, start_date, end_date, cfp_deadline, website, description, source, discovered_at, last_seen (no year column)
    query = """
        SELECT name, location, country, region, start_date, end_date,
               cfp_deadline, website, description, source
        FROM conferences
        WHERE 1=1
    """
    params = []

    if region:
        query += " AND region = ?"
        params.append(region)
    if year:
        query += " AND start_date LIKE ?"
        params.append(f"{year}%")
    if search:
        query += " AND (name LIKE ? OR location LIKE ? OR description LIKE ?)"
        p = f"%{search}%"
        params.extend([p, p, p])

    # Upcoming first (by start_date ASC), then past (by start_date DESC)
    query += """
        ORDER BY (start_date >= date('now')) DESC,
                 CASE WHEN start_date >= date('now') THEN start_date END ASC,
                 CASE WHEN start_date < date('now') THEN start_date END DESC,
                 name ASC
    """

    cursor.execute(query, params)
    rows = cursor.fetchall()
    today = datetime.now().strftime("%Y-%m-%d")
    conferences = []
    for r in rows:
        d = dict(r)
        start = d.get("start_date")
        d["year"] = int(start[:4]) if start and len(start) >= 4 else None
        d["is_upcoming"] = (start or "") >= today if start else True
        d["name"] = sanitize_name(d.get("name"))
        d["location"] = sanitize_location(d.get("location"))
        d["country"] = sanitize_location(d.get("country"))
        conferences.append(d)

    # Summary counts by region (same filters)
    sum_query = "SELECT region, COUNT(*) as count FROM conferences WHERE 1=1"
    sum_params = []
    if region:
        sum_query += " AND region = ?"
        sum_params.append(region)
    if year:
        sum_query += " AND start_date LIKE ?"
        sum_params.append(f"{year}%")
    if search:
        sum_query += " AND (name LIKE ? OR location LIKE ? OR description LIKE ?)"
        p = f"%{search}%"
        sum_params.extend([p, p, p])
    sum_query += " GROUP BY region ORDER BY count DESC"
    cursor.execute(sum_query, sum_params)
    summary = {row["region"]: row["count"] for row in cursor.fetchall()}

    conn.close()
    return jsonify({"conferences": conferences, "summary": summary})


@app.route("/api/summary")
def api_summary():
    conn = get_db()
    if not conn:
        return jsonify({"regions": {}, "total": 0})

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT region, COUNT(DISTINCT name || COALESCE(start_date, '')) as count
        FROM conferences
        WHERE start_date >= '2025-01-01' AND start_date < '2028-01-01'
        GROUP BY region
        ORDER BY count DESC
    """
    )
    regions = {row["region"]: row["count"] for row in cursor.fetchall()}
    total = sum(regions.values())
    conn.close()
    return jsonify({"regions": regions, "total": total})


@app.route("/api/health")
def api_health():
    """Debug: see if DB exists and how many rows (for Render troubleshooting)."""
    db_exists = os.path.exists(DATABASE)
    count = None
    if db_exists:
        try:
            conn = sqlite3.connect(DATABASE)
            count = conn.execute("SELECT COUNT(*) FROM conferences").fetchone()[0]
            conn.close()
        except Exception as e:
            count = str(e)
    return jsonify({
        "db_exists": db_exists,
        "db_path": DATABASE,
        "conferences_count": count,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
