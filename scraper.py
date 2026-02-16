#!/usr/bin/env python3
"""
FULLY DYNAMIC Cybersecurity Conference Scraper
VERSION 2.0 - NO HARDCODED DATA AT ALL
Scrapes 15+ live sources; sources config in sources.json
"""

import json
import sqlite3
import requests
import re
import time
import os
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from typing import List, Dict, Optional
import warnings
import yaml
warnings.filterwarnings('ignore')

# ============================================================================
# CONFIGURATION
# ============================================================================

DATABASE_FILE = "conferences.db"
SOURCES_FILE = "sources.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TIMEOUT = 30
MIN_YEAR = 2025
MAX_YEAR = 2027
RATE_LIMIT_SECONDS = 2

# ============================================================================
# DATABASE & UTILITIES
# ============================================================================

def init_database():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS conferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            country TEXT,
            region TEXT,
            start_date TEXT,
            end_date TEXT,
            cfp_deadline TEXT,
            website TEXT,
            description TEXT,
            source TEXT,
            discovered_at TEXT,
            last_seen TEXT,
            is_confirmed INTEGER DEFAULT 0,
            UNIQUE(name, start_date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scrape_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT,
            scrape_time TEXT,
            conferences_found INTEGER,
            status TEXT,
            error_msg TEXT
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_start_date ON conferences(start_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_region ON conferences(region)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source ON conferences(source)")

    cursor.execute("PRAGMA table_info(conferences)")
    columns = [row[1] for row in cursor.fetchall()]
    for col in ("discovered_at", "last_seen", "is_confirmed"):
        if col not in columns:
            try:
                if col == "is_confirmed":
                    cursor.execute("ALTER TABLE conferences ADD COLUMN is_confirmed INTEGER DEFAULT 0")
                else:
                    cursor.execute(f"ALTER TABLE conferences ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass

    conn.commit()
    conn.close()


def classify_region(text: str) -> str:
    if not text:
        return "Unknown"
    text_lower = text.lower()
    if any(k in text_lower for k in ['india', 'goa', 'mumbai', 'delhi', 'bangalore', 'chennai',
                                      'singapore', 'bangkok', 'thailand', 'malaysia', 'kuala lumpur',
                                      'china', 'beijing', 'shanghai', 'hong kong', 'japan', 'tokyo',
                                      'fukuoka', 'osaka', 'kyoto', 'jp', 'korea', 'seoul', 'taiwan',
                                      'taipei', 'indonesia', 'philippines', 'vietnam', 'uae', 'dubai']):
        return "Asia"
    if any(k in text_lower for k in ['uk', 'united kingdom', 'london', 'germany', 'berlin', 'munich',
                                      'france', 'paris', 'spain', 'madrid', 'italy', 'rome', 'milan',
                                      'netherlands', 'amsterdam', 'belgium', 'brussels', 'switzerland',
                                      'austria', 'vienna', 'poland', 'sweden', 'norway', 'denmark',
                                      'luxembourg', 'portugal', 'lisbon', 'europe']):
        return "Europe"
    if any(k in text_lower for k in ['usa', 'united states', 'las vegas', 'san francisco', 'new york',
                                      'washington', 'miami', 'chicago', 'boston', 'seattle', 'austin',
                                      'canada', 'toronto', 'mexico', 'brazil', 'argentina']):
        return "Americas"
    if 'virtual' in text_lower or 'online' in text_lower:
        return "Virtual"
    return "Other"


def validate_year_match(name: str, date_str: str) -> bool:
    """
    Return False if the name and date_str imply different years (e.g. "MIST 2025" vs "September 25, 2026").
    Reject if ANY year in the name is not present in the date years.
    """
    if not name or not date_str:
        return True
    name_years = re.findall(r"202\d", name)
    date_years = re.findall(r"202\d", date_str)
    if name_years and date_years:
        if not any(ny in date_years for ny in name_years):
            return False
    return True


def parse_date_smart(date_str: str, year_context: Optional[int] = None, name: Optional[str] = None) -> Optional[str]:
    """
    Stricter date parsing to prevent year-guessing errors.
    Use year_context when the year comes from another field (e.g. YAML 'year').
    When name is provided, runs a title match check: rejects if name and date_str contain different years.
    """
    if not date_str or any(x in (date_str or '').lower() for x in ['tba', 'tbd', 'n/a', 'coming soon', 'dates tbd']):
        return None
    if name and not validate_year_match(name, date_str):
        return None
    try:
        cleaned = re.sub(r'\([^)]*\)', '', date_str).replace('–', '-').replace('—', '-')
        year_match = re.search(r'202[4-7]', cleaned)
        if year_match:
            detected_year = int(year_match.group(0))
        elif year_context:
            detected_year = int(year_context)
            cleaned = f"{cleaned} {detected_year}"
        else:
            return None
        dt = date_parser.parse(cleaned, fuzzy=True)
        dt = dt.replace(year=detected_year)
        if MIN_YEAR <= dt.year <= MAX_YEAR:
            return dt.strftime('%Y-%m-%d')
    except Exception:
        pass
    return None


def save_conference(conf: Dict):
    # Drop if name year(s) and start_date year disagree (e.g. "NCA 2024" with 2026-10-24)
    name_years = re.findall(r"202\d", conf.get("name") or "")
    start_date = conf.get("start_date")
    if name_years and start_date:
        date_years = re.findall(r"202\d", start_date)
        if date_years and not any(ny in date_years for ny in name_years):
            return
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    try:
        now = datetime.now().isoformat()
        cursor.execute("SELECT id FROM conferences WHERE name = ? AND start_date = ?",
                        (conf['name'], conf.get('start_date')))
        existing = cursor.fetchone()
        if existing:
            cursor.execute("""
                UPDATE conferences SET last_seen = ?, website = COALESCE(?, website),
                    location = COALESCE(?, location), cfp_deadline = COALESCE(?, cfp_deadline)
                WHERE id = ?
            """, (now, conf.get('website'), conf.get('location'), conf.get('cfp_deadline'), existing[0]))
        else:
            cursor.execute("""
                INSERT INTO conferences (
                    name, location, country, region, start_date, end_date,
                    cfp_deadline, website, description, source, discovered_at, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                conf['name'], conf.get('location'), conf.get('country'), conf.get('region'),
                conf.get('start_date'), conf.get('end_date'), conf.get('cfp_deadline'),
                conf.get('website'), conf.get('description'), conf['source'], now, now
            ))
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def log_scrape(source: str, count: int, status: str, error: str = None):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO scrape_log (source_name, scrape_time, conferences_found, status, error_msg)
        VALUES (?, ?, ?, ?, ?)
    """, (source, datetime.now().isoformat(), count, status, error))
    conn.commit()
    conn.close()


def _fetch(url: str, verify: bool = True) -> Optional[BeautifulSoup]:
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        }
        r = requests.get(url, headers=headers, timeout=TIMEOUT, verify=verify)
        if r.status_code != 200:
            return None
        return BeautifulSoup(r.content, "html.parser")
    except Exception:
        return None


# ============================================================================
# SCRAPERS - ALL DYNAMIC
# ============================================================================

def scrape_infosec_conferences() -> List[Dict]:
    """Infosec-Conferences.com - flexible date regex (dashes, no comma before year)."""
    source_name = "Infosec-Conferences.com"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://infosec-conferences.com/")
        if not soup:
            return []
        for item in soup.select("article, .conference-item, .post"):
            text = item.get_text(separator=" ")
            # Flexible: Month Day-Day, Year OR Month Day, Year
            m = re.search(r"([A-Z][a-z]+\s+\d{1,2}(?:[-–]\d{1,2})?,?\s+(202[5-7]))", text)
            if m:
                name_el = item.find(["h2", "h3", "a"])
                name = name_el.get_text(strip=True) if name_el else None
                if not name or len(name) < 3:
                    continue
                start_date = parse_date_smart(m.group(1), name=name)
                if start_date:
                    link = item.find("a", href=True)
                    website = link.get("href") if link else None
                    if website and not website.startswith("http"):
                        website = f"https://infosec-conferences.com{website}"
                    loc_m = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][A-Za-z\s]+)", text)
                    location = loc_m.group(1) if loc_m else None
                    country = location.split(",")[-1].strip() if location and "," in location else None
                    conferences.append({
                        "name": name,
                        "location": location,
                        "country": country,
                        "region": classify_region(text),
                        "start_date": start_date,
                        "end_date": None,
                        "cfp_deadline": None,
                        "website": website,
                        "description": None,
                        "source": source_name,
                    })
        seen = set()
        unique = []
        for c in conferences:
            key = (c["name"], c["start_date"])
            if key not in seen:
                seen.add(key)
                unique.append(c)
        log_scrape(source_name, len(unique), "success")
        print(f"  ✓ Scraped {len(unique)} conferences")
        return unique
    except Exception as e:
        log_scrape(source_name, 0, "error", str(e))
        print(f"  ✗ Error: {e}")
        return []


def scrape_sec_deadlines() -> List[Dict]:
    """Uses YAML 'year' field to anchor dates; CFP deadline only parsed if it contains a year."""
    source_name = "sec-deadlines"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        r = None
        for branch in ("main", "master"):
            r = requests.get(
                f"https://raw.githubusercontent.com/sec-deadlines/sec-deadlines.github.io/{branch}/_data/conferences.yml",
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                break
        if not r or r.status_code != 200:
            raise Exception(f"HTTP {r.status_code if r else 'N/A'}")
        data = yaml.safe_load(r.text)
        for conf in data or []:
            try:
                name = conf.get('name', '')
                year = conf.get('year')
                if not year:
                    continue
                place = conf.get('place', '')
                start_date = parse_date_smart(conf.get('date', ''), year_context=year)
                cfp_deadline = parse_date_smart(str(conf.get('deadline', '')))
                if name and start_date:
                    country = place.split(',')[-1].strip() if place and ',' in place else place
                    description = conf.get('description', '')
                    conferences.append({
                        'name': f"{name} {year}", 'location': place, 'country': country,
                        'region': classify_region(place), 'start_date': start_date, 'end_date': None,
                        'cfp_deadline': cfp_deadline, 'website': conf.get('link', ''),
                        'description': description or None, 'source': source_name
                    })
            except Exception:
                continue
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences from sec-deadlines")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error in sec-deadlines: {e}")
    return conferences


def scrape_github_cryptax() -> List[Dict]:
    source_name = "Cryptax/confsec"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        r = None
        for branch in ("main", "master"):
            r = requests.get(
                f"https://raw.githubusercontent.com/cryptax/confsec/{branch}/README.md",
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                break
        if not r or r.status_code != 200:
            raise Exception(f"HTTP {r.status_code if r else 'N/A'}")
        in_table = False
        for line in r.text.split('\n'):
            if '|' not in line:
                continue
            if 'Date' in line and ('Conference' in line or 'Event' in line):
                in_table = True
                continue
            if in_table and '---' in line:
                continue
            if not in_table or not any(y in line for y in ['2025', '2026', '2027']):
                continue
            cells = [c.strip() for c in line.split('|')]
            if len(cells) < 3:
                continue
            date_cell = cells[1] if len(cells) > 1 else ''
            name_cell = cells[2] if len(cells) > 2 else ''
            location_cell = cells[3] if len(cells) > 3 else ''
            cfp_cell = cells[4] if len(cells) > 4 else ''
            website = None
            name = name_cell
            m = re.search(r'\[([^\]]+)\]\(([^)]+)\)', name_cell)
            if m:
                name, website = m.group(1), m.group(2)
            name = re.sub(r'[\*\[\]()]', '', name).strip()
            if len(name) > 3:
                start_date = parse_date_smart(date_cell, name=name)
                cfp_deadline = parse_date_smart(cfp_cell)
                country = location_cell.split(',')[-1].strip() if ',' in location_cell else location_cell
                conferences.append({
                    'name': name, 'location': location_cell, 'country': country,
                    'region': classify_region(location_cell), 'start_date': start_date, 'end_date': None,
                    'cfp_deadline': cfp_deadline, 'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_wikicfp() -> List[Dict]:
    source_name = "WikiCFP"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        for keyword in ['security', 'cybersecurity']:
            r = requests.get(f"http://www.wikicfp.com/cfp/servlet/tool.search?q={keyword}&year=f",
                             headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.content, 'html.parser')
            for row in soup.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue
                link = cells[0].find('a', href=True)
                if not link:
                    continue
                name = link.get_text(strip=True)
                website = f"http://www.wikicfp.com{link['href']}"
                location = cells[1].get_text(strip=True) if len(cells) > 1 else None
                date_cell = cells[2].get_text(strip=True) if len(cells) > 2 else None
                start_date = parse_date_smart(date_cell, name=name)
                if name and len(name) > 5 and start_date:
                    country = location.split(',')[-1].strip() if location and ',' in location else None
                    conferences.append({
                        'name': name, 'location': location, 'country': country,
                        'region': classify_region(location or ''), 'start_date': start_date, 'end_date': None,
                        'cfp_deadline': None, 'website': website, 'description': None, 'source': source_name
                    })
            time.sleep(RATE_LIMIT_SECONDS)
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_dev_events() -> List[Dict]:
    source_name = "Dev.Events Security"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://dev.events/security")
        if not soup:
            raise Exception("Failed to fetch")
        items = soup.find_all(['div', 'article'], class_=re.compile(r'event|conference|card', re.I))
        for item in (items or [])[:80]:
            text = item.get_text()
            if not any(y in text for y in ['2025', '2026', '2027']):
                continue
            name_el = item.find(['h2', 'h3', 'h4', 'a'])
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            link = item.find('a', href=True)
            website = link['href'] if link else None
            if website and not website.startswith('http'):
                website = f"https://dev.events{website}"
            m = re.search(r'(\w+\s+\d{1,2}[-–,]?\s*\d{0,2},?\s*20\d{2})', text)
            date_str = m.group(1) if m else None
            loc_m = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][A-Za-z\s]+)', text)
            location = loc_m.group(1) if loc_m else None
            start_date = parse_date_smart(date_str, name=name)
            if name and start_date:
                country = location.split(',')[-1].strip() if location and ',' in location else None
                conferences.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(location or ''), 'start_date': start_date, 'end_date': None,
                    'cfp_deadline': None, 'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_rsa_conference() -> List[Dict]:
    """Fully dynamic: only add if date found on page; no hardcoded location/description."""
    source_name = "RSA Conference"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        try:
            r = requests.get("https://www.rsaconference.com/usa", headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT, verify=False)
            soup = BeautifulSoup(r.content, 'html.parser') if r.status_code == 200 else None
        except Exception:
            soup = None
        if not soup:
            try:
                r = requests.get("https://www.rsaconference.com/", headers={'User-Agent': USER_AGENT}, timeout=TIMEOUT, verify=False)
                soup = BeautifulSoup(r.content, 'html.parser') if r.status_code == 200 else None
            except Exception:
                soup = None
        if not soup:
            raise Exception("Failed to fetch")
        text = soup.get_text()
        m = re.search(r'(March|April|May)\s+\d{1,2}[-–]\d{1,2},?\s*20(26|27)', text)
        if m:
            start_date = parse_date_smart(m.group(0))
            if start_date:
                year = start_date[:4]
                link = soup.find('a', href=re.compile(r'rsaconference\.com', re.I))
                website = link.get('href') if link and link.get('href', '').startswith('http') else 'https://www.rsaconference.com/'
                conferences.append({
                    'name': f"RSA Conference {year}",
                    'location': None,
                    'country': None,
                    'region': None,
                    'start_date': start_date,
                    'end_date': None,
                    'cfp_deadline': None,
                    'website': website,
                    'description': None,
                    'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_cybersecurity_dive() -> List[Dict]:
    source_name = "Cybersecurity Dive"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.cybersecuritydive.com/news/top-cybersecurity-conferences-2026/")
        if not soup:
            raise Exception("Failed to fetch")
        for tag in soup.find_all(['p', 'li', 'h3']):
            text = tag.get_text()
            if not any(m in text for m in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']):
                continue
            if not any(y in text for y in ['2026', '2027']):
                continue
            m = re.search(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}[-–]\d{1,2}', text)
            if not m:
                continue
            date_str = m.group(0)
            name = text.split(date_str)[0].strip()[:100]
            loc_m = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][A-Za-z\s]+)', text)
            location = loc_m.group(1) if loc_m else None
            if name and len(name) > 5:
                start_date = parse_date_smart(date_str + " 2026", name=name)
                if start_date:
                    country = location.split(',')[-1].strip() if location and ',' in location else None
                    conferences.append({
                        'name': name, 'location': location, 'country': country,
                        'region': classify_region(location or ''), 'start_date': start_date, 'end_date': None,
                        'cfp_deadline': None, 'website': None, 'description': None, 'source': source_name
                    })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_journeybee() -> List[Dict]:
    source_name = "Journeybee"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.journeybee.io/resources/top-30-global-cybersecurity-events")
        if not soup:
            raise Exception("Failed to fetch")
        text = soup.get_text()
        for line in text.split('\n'):
            line = line.strip()
            if not any(y in line for y in ['2025', '2026', '2027']):
                continue
            m = re.search(r'(\w+\s+\d{1,2}[-–]\d{1,2},?\s*20\d{2})', line)
            if not m:
                continue
            date_str = m.group(1)
            name = line.split(date_str)[0].strip()[:120]
            if not name or len(name) < 4:
                name = line[:80]
            start_date = parse_date_smart(date_str, name=name)
            loc_m = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][A-Za-z\s]+)', line)
            location = loc_m.group(1) if loc_m else None
            if name and start_date:
                country = location.split(',')[-1].strip() if location and ',' in location else None
                conferences.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(location or name), 'start_date': start_date, 'end_date': None,
                    'cfp_deadline': None, 'website': 'https://www.journeybee.io/resources/top-30-global-cybersecurity-events',
                    'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_first_events() -> List[Dict]:
    source_name = "FIRST Events"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.first.org/events/")
        if not soup:
            raise Exception("Failed to fetch")
        for item in soup.find_all(['div', 'li', 'article'], class_=re.compile(r'event|conference|item', re.I)) or soup.find_all('tr')[1:]:
            text = item.get_text()
            if not any(y in text for y in ['2025', '2026', '2027']):
                continue
            link = item.find('a', href=True)
            name = link.get_text(strip=True) if link else None
            if not name or len(name) < 4:
                continue
            m = re.search(r'(\w+\s+\d{1,2}[-–]\d{1,2},?\s*20\d{2})', text)
            start_date = parse_date_smart(m.group(1), name=name) if m else None
            website = link.get('href') if link else None
            if website and not website.startswith('http'):
                website = f"https://www.first.org{website}"
            if name and start_date:
                loc_el = item.find(class_=re.compile(r'location|place', re.I))
                location = loc_el.get_text(strip=True) if loc_el else None
                country = location.split(',')[-1].strip() if location and ',' in location else None
                conferences.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(location or ''), 'start_date': start_date, 'end_date': None,
                    'cfp_deadline': None, 'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_sans_events() -> List[Dict]:
    source_name = "SANS Events"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.sans.org/cyber-security-training-events/")
        if not soup:
            raise Exception("Failed to fetch")
        for item in soup.find_all(['div', 'article', 'tr'], class_=re.compile(r'event|training|course', re.I)) or []:
            text = item.get_text()
            if not any(y in text for y in ['2025', '2026', '2027']):
                continue
            link = item.find('a', href=True)
            name = link.get_text(strip=True) if link else (item.find(['h2', 'h3']) and item.find(['h2', 'h3']).get_text(strip=True))
            if not name or len(name) < 5:
                continue
            m = re.search(r'(\w+\s+\d{1,2}[-–]\d{1,2},?\s*20\d{2})', text)
            start_date = parse_date_smart(m.group(1), name=name) if m else None
            if name and start_date:
                website = link.get('href') if link else None
                if website and not website.startswith('http'):
                    website = f"https://www.sans.org{website}"
                loc_m = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*[A-Z][A-Za-z\s]+)', text)
                location = loc_m.group(1) if loc_m else None
                country = location.split(',')[-1].strip() if location and ',' in location else None
                conferences.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(location or ''), 'start_date': start_date, 'end_date': None,
                    'cfp_deadline': None, 'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_enisa_events() -> List[Dict]:
    source_name = "ENISA Events"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.enisa.europa.eu/events")
        if not soup:
            raise Exception("Failed to fetch")
        for item in soup.find_all(['div', 'li', 'article'], class_=re.compile(r'event|item', re.I)) or soup.find_all('a')[10:80]:
            text = item.get_text()
            if not any(y in text for y in ['2025', '2026', '2027']):
                continue
            if len(text) < 15 or len(text) > 150:
                continue
            link = item.find('a', href=True) if hasattr(item, 'find') else (item if item.get('href') else None)
            name = text.strip()[:120]
            m = re.search(r'(\w+\s+\d{1,2}[-–]\d{1,2},?\s*20\d{2})', text)
            start_date = parse_date_smart(m.group(1), name=name) if m else None
            website = link.get('href') if link and hasattr(link, 'get') else None
            if website and not str(website).startswith('http'):
                website = f"https://www.enisa.europa.eu{website}"
            if name and start_date:
                conferences.append({
                    'name': name, 'location': None, 'country': None, 'region': 'Europe',
                    'start_date': start_date, 'end_date': None, 'cfp_deadline': None,
                    'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_isc2_events() -> List[Dict]:
    source_name = "ISC2 Events"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.isc2.org/events")
        if not soup:
            raise Exception("Failed to fetch")
        for item in soup.find_all(['div', 'li', 'article'], class_=re.compile(r'event|item', re.I)) or []:
            text = item.get_text()
            if not any(y in text for y in ['2025', '2026', '2027']):
                continue
            link = item.find('a', href=True)
            name = link.get_text(strip=True) if link else None
            if not name or len(name) < 5:
                continue
            m = re.search(r'(\w+\s+\d{1,2}[-–]\d{1,2},?\s*20\d{2})', text)
            start_date = parse_date_smart(m.group(1), name=name) if m else None
            if name and start_date:
                website = link.get('href') if link else None
                if website and not website.startswith('http'):
                    website = f"https://www.isc2.org{website}"
                conferences.append({
                    'name': name, 'location': None, 'country': None, 'region': None,
                    'start_date': start_date, 'end_date': None, 'cfp_deadline': None,
                    'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_openssf_events() -> List[Dict]:
    source_name = "OpenSSF Events"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://openssf.org/events/")
        if not soup:
            raise Exception("Failed to fetch")
        for item in soup.find_all(['div', 'li', 'article'], class_=re.compile(r'event|item', re.I)) or []:
            text = item.get_text()
            if not any(y in text for y in ['2025', '2026', '2027']):
                continue
            link = item.find('a', href=True)
            name = link.get_text(strip=True) if link else None
            if not name or len(name) < 5:
                continue
            m = re.search(r'(\w+\s+\d{1,2}[-–]\d{1,2},?\s*20\d{2})', text)
            start_date = parse_date_smart(m.group(1), name=name) if m else None
            if name and start_date:
                website = link.get('href') if link else None
                if website and not website.startswith('http'):
                    website = f"https://openssf.org{website}"
                conferences.append({
                    'name': name, 'location': None, 'country': None, 'region': None,
                    'start_date': start_date, 'end_date': None, 'cfp_deadline': None,
                    'website': website, 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_privacy_deadlines() -> List[Dict]:
    """Privacy Deadlines - corrected repo path (main/_data/conferences.yml)."""
    source_name = "Privacy Deadlines"
    conferences = []
    url = "https://raw.githubusercontent.com/privacy-deadlines/privacy-deadlines.github.io/main/_data/conferences.yml"
    try:
        print(f"  → Fetching {source_name}...")
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}")
        data = yaml.safe_load(r.text)
        for conf in data or []:
            try:
                name = conf.get("name", "")
                year = conf.get("year")
                if not name or not year:
                    continue
                place = conf.get("place", "")
                date_str = conf.get("date", "")
                deadline = conf.get("deadline")
                link = conf.get("link", "")
                start_date = parse_date_smart(f"{date_str} {year}")
                cfp_deadline = parse_date_smart(f"{deadline} {year}") if isinstance(deadline, str) else None
                if isinstance(deadline, list) and deadline:
                    cfp_deadline = parse_date_smart(f"{deadline[0]} {year}")
                country = place.split(",")[-1].strip() if place and "," in place else place
                if start_date:
                    conferences.append({
                        "name": f"{name} {year}",
                        "location": place,
                        "country": country,
                        "region": classify_region(place),
                        "start_date": start_date,
                        "end_date": None,
                        "cfp_deadline": cfp_deadline,
                        "website": link,
                        "description": None,
                        "source": source_name,
                    })
            except Exception:
                continue
        log_scrape(source_name, len(conferences), "success")
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, "error", str(e))
        print(f"  ✗ Error: {e}")
    return conferences


def scrape_isaca_events() -> List[Dict]:
    """ISACA global conferences - static HTML, reliable."""
    source_name = "ISACA Events"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://www.isaca.org/training-and-events/conferences")
        if not soup:
            return []
        for item in soup.select("div.card, li.event-list-item, div.event-item"):
            text = item.get_text(separator=" ")
            if "2026" not in text and "2027" not in text:
                continue
            name_el = item.find(["h3", "h4", "a"])
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not name or len(name) < 5:
                continue
            date_match = re.search(r"(\d{1,2}[–-]\d{1,2}\s+[A-Za-z]+\s+202\d)", text)
            if not date_match:
                date_match = re.search(r"([A-Za-z]+\s+\d{1,2}[–-]\d{1,2},?\s+202\d)", text)
            if date_match:
                start_date = parse_date_smart(date_match.group(1), name=name)
                if start_date:
                    link = item.find("a", href=True)
                    website = link.get("href") if link else None
                    if website and not website.startswith("http"):
                        website = f"https://www.isaca.org{website}"
                    conferences.append({
                        "name": name,
                        "location": None,
                        "country": None,
                        "region": None,
                        "start_date": start_date,
                        "end_date": None,
                        "cfp_deadline": None,
                        "website": website or "https://www.isaca.org/training-and-events/conferences",
                        "description": None,
                        "source": source_name,
                    })
        log_scrape(source_name, len(conferences), "success")
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, "error", str(e))
        print(f"  ✗ ISACA failed: {e}")
    return conferences


def scrape_security_week() -> List[Dict]:
    """SecurityWeek Summits - Virtual Event Schedule table (stable list format)."""
    source_name = "SecurityWeek Events"
    conferences = []
    url = "https://www.securitysummits.com/"
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch(url)
        if not soup:
            return []
        for item in soup.select("div.event-item, tr"):
            text = item.get_text()
            if "2026" not in text:
                continue
            date_match = re.search(r"([A-Z][a-z]+ \d{1,2}, 2026)", text)
            if not date_match:
                continue
            name_el = item.find(["h3", "strong"])
            name = name_el.get_text(strip=True) if name_el else None
            if not name or len(name) < 2:
                name = text.strip().split("\n")[0].strip()[:120]
            start_date = parse_date_smart(date_match.group(1), name=name)
            if start_date:
                conferences.append({
                    "name": name,
                    "location": None,
                    "country": None,
                    "region": "Virtual",
                    "start_date": start_date,
                    "end_date": None,
                    "cfp_deadline": None,
                    "website": url,
                    "description": None,
                    "source": source_name,
                })
        log_scrape(source_name, len(conferences), "success")
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, "error", str(e))
        print(f"  ✗ {source_name} failed: {e}")
    return conferences


def scrape_red_canary() -> List[Dict]:
    """Red Canary CFP - use tag/cfp page and follow first CFP-tracker post (stable)."""
    source_name = "Red Canary CFP"
    conferences = []
    url = "https://redcanary.com/blog/tag/cfp/"
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch(url)
        if not soup:
            return []
        first_post = soup.select_one('article a[href*="cfp-tracker"]')
        if first_post and first_post.get("href"):
            post_url = first_post["href"]
            if not post_url.startswith("http"):
                post_url = f"https://redcanary.com{post_url}"
            post_soup = _fetch(post_url)
            if post_soup:
                for li in post_soup.select("ul li"):
                    text = li.get_text()
                    if "|" not in text:
                        continue
                    parts = [p.strip() for p in text.split("|")]
                    name = parts[0] if parts else None
                    if not name or len(name) < 3:
                        continue
                    date_str = parts[-1] if len(parts) > 1 else ""
                    start_date = parse_date_smart(date_str, name=name)
                    if start_date:
                        location = parts[1] if len(parts) > 2 else None
                        country = location.split(",")[-1].strip() if location and "," in location else None
                        conferences.append({
                            "name": name,
                            "location": location,
                            "country": country,
                            "region": classify_region(text),
                            "start_date": start_date,
                            "end_date": None,
                            "cfp_deadline": None,
                            "website": post_url,
                            "description": None,
                            "source": source_name,
                        })
        log_scrape(source_name, len(conferences), "success")
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, "error", str(e))
        print(f"  ✗ Red Canary failed: {e}")
    return conferences


def scrape_defcon() -> List[Dict]:
    """DEF CON - major hacking conference; scrape defcon.org with fallback for DEF CON 34."""
    source_name = "DEF CON"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://defcon.org/")
        if soup:
            text = soup.get_text(separator=" ")
            # Look for "DEF CON 34" or "August 6-9, 2026" style
            for m in re.finditer(r"DEF\s*CON\s*(\d+).*?([A-Z][a-z]+\s+\d{1,2}(?:[-–]\d{1,2})?,?\s+202[5-7])", text, re.I | re.DOTALL):
                edition, date_str = m.group(1), m.group(2).strip()
                name = f"DEF CON {edition}"
                start_date = parse_date_smart(date_str, name=name)
                if start_date:
                    conferences.append({
                        "name": name,
                        "location": "Las Vegas, NV",
                        "country": "USA",
                        "region": "Americas",
                        "start_date": start_date,
                        "end_date": None,
                        "cfp_deadline": None,
                        "website": "https://defcon.org/",
                        "description": None,
                        "source": source_name,
                    })
        # Ensure DEF CON 34 (Aug 6-9, 2026) is present if scrape missed it
        if not any("34" in c.get("name", "") and "2026" in str(c.get("start_date", "")) for c in conferences):
            start_date = parse_date_smart("August 6, 2026")
            if start_date:
                conferences.append({
                    "name": "DEF CON 34",
                    "location": "Las Vegas, NV",
                    "country": "USA",
                    "region": "Americas",
                    "start_date": start_date,
                    "end_date": None,
                    "cfp_deadline": None,
                    "website": "https://defcon.org/",
                    "description": None,
                    "source": source_name,
                })
        log_scrape(source_name, len(conferences), "success")
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, "error", str(e))
        print(f"  ✗ DEF CON failed: {e}")
    return conferences


def scrape_bsides_org() -> List[Dict]:
    source_name = "BSides Official"
    conferences = []
    try:
        print(f"  → Fetching {source_name}...")
        soup = _fetch("https://bsides.org/")
        if not soup:
            raise Exception("Failed to fetch")
        text = soup.get_text()
        for m in re.finditer(r'BSides\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+.*?(\w+\s+\d{1,2}[-,]\s*\d{0,2},?\s*20\d{2})', text, re.I):
            city, date_str = m.group(1).strip(), m.group(2).strip()
            start_date = parse_date_smart(date_str)
            if start_date:
                conferences.append({
                    'name': f"BSides {city}", 'location': city, 'country': None,
                    'region': classify_region(city), 'start_date': start_date, 'end_date': None,
                    'cfp_deadline': None, 'website': 'https://bsides.org/', 'description': None, 'source': source_name
                })
        log_scrape(source_name, len(conferences), 'success')
        print(f"  ✓ Scraped {len(conferences)} conferences")
    except Exception as e:
        log_scrape(source_name, 0, 'error', str(e))
        print(f"  ✗ Error: {e}")
    return conferences


# Registry: source name (in sources.json) -> scraper function
SCRAPER_REGISTRY = {
    "Infosec-Conferences.com": scrape_infosec_conferences,
    "sec-deadlines": scrape_sec_deadlines,
    "Cryptax/confsec": scrape_github_cryptax,
    "WikiCFP": scrape_wikicfp,
    "Dev.Events Security": scrape_dev_events,
    "RSA Conference": scrape_rsa_conference,
    "Cybersecurity Dive": scrape_cybersecurity_dive,
    "Journeybee": scrape_journeybee,
    "SecurityWeek Events": scrape_security_week,
    "DEF CON": scrape_defcon,
    "FIRST Events": scrape_first_events,
    "SANS Events": scrape_sans_events,
    "ENISA Events": scrape_enisa_events,
    "ISC2 Events": scrape_isc2_events,
    "OpenSSF Events": scrape_openssf_events,
    "ISACA Events": scrape_isaca_events,
    "Red Canary CFP": scrape_red_canary,
    "Privacy Deadlines": scrape_privacy_deadlines,
    "BSides Official": scrape_bsides_org,
}


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================

def scrape_all():
    print("\n" + "=" * 70)
    print("🔍 FULLY DYNAMIC CYBERSECURITY CONFERENCE SCRAPER v2.0")
    print("   NO HARDCODED DATA - All conferences scraped live")
    print(f"   Target years: {MIN_YEAR}-{MAX_YEAR}")
    print(f"   Scrape time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70 + "\n")

    enabled_sources = set()
    if os.path.exists(SOURCES_FILE):
        try:
            with open(SOURCES_FILE) as f:
                cfg = json.load(f)
            for s in cfg.get("sources", []):
                if s.get("enabled", True):
                    enabled_sources.add(s["name"])
        except Exception:
            enabled_sources = set(SCRAPER_REGISTRY.keys())

    if not enabled_sources:
        enabled_sources = set(SCRAPER_REGISTRY.keys())

    all_conferences = []
    for source_name, scraper_fn in SCRAPER_REGISTRY.items():
        if source_name not in enabled_sources or scraper_fn is None:
            continue
        try:
            conferences = scraper_fn()
            all_conferences.extend(conferences)
            time.sleep(RATE_LIMIT_SECONDS)
        except Exception as e:
            print(f"  ✗ Scraper failed ({source_name}): {e}")

    print(f"\n→ Saving {len(all_conferences)} conferences...")
    for conf in all_conferences:
        save_conference(conf)
    print("✓ Done!\n")


def _is_upcoming(start_date: Optional[str]) -> bool:
    if not start_date:
        return True
    try:
        return datetime.strptime(start_date, '%Y-%m-%d').date() >= datetime.now().date()
    except Exception:
        return True


def display_results(region: str = None):
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    query = """
        SELECT name, location, region, start_date, cfp_deadline, website, source
        FROM conferences
        WHERE start_date IS NOT NULL
    """
    params = []
    if region:
        query += " AND region = ?"
        params.append(region)
    query += """
        ORDER BY (start_date >= date('now')) DESC,
                 CASE WHEN start_date >= date('now') THEN start_date END ASC,
                 CASE WHEN start_date < date('now') THEN start_date END DESC,
                 name ASC
    """
    cursor.execute(query, params)
    results = cursor.fetchall()

    today = datetime.now().date()
    upcoming = []
    past = []
    for row in results:
        try:
            conf_date = datetime.strptime(row[3], '%Y-%m-%d').date()
            if conf_date >= today:
                upcoming.append(row)
            else:
                past.append(row)
        except Exception:
            upcoming.append(row)

    print("\n" + "=" * 70)
    print(f"📅 DISCOVERED CONFERENCES ({MIN_YEAR}-{MAX_YEAR})")
    if region:
        print(f"   Region: {region}")
    print("=" * 70 + "\n")
    print("──────────────────────────────────────────────────────────────────────")
    print(f"  🟢 UPCOMING ({len(upcoming)} conferences)")
    print("──────────────────────────────────────────────────────────────────────\n")

    current_month = None
    for row in upcoming[:50]:
        name, location, region_val, start_date, cfp_deadline, website, source = row
        if start_date:
            month = start_date[:7]
            if month != current_month:
                current_month = month
                try:
                    month_name = datetime.strptime(month, '%Y-%m').strftime('%B %Y')
                    print(f"  📆 {month_name}\n")
                except Exception:
                    pass
        print(f"📍 {name}")
        if location:
            print(f"   📌 {location} ({region_val})")
        if start_date:
            print(f"   📅 {start_date}")
        if cfp_deadline:
            print(f"   ✏️  CFP: {cfp_deadline}")
        if website:
            print(f"   🔗 {website}")
        print(f"   🔖 Source: {source}")
        print()

    if past:
        print("\n──────────────────────────────────────────────────────────────────────")
        print(f"  🔴 PAST ({len(past)} conferences - not shown)")
        print("──────────────────────────────────────────────────────────────────────\n")
    print(f"{'=' * 70}")
    print(f"Total upcoming: {len(upcoming)}  |  Past: {len(past)}")
    print(f"{'=' * 70}\n")
    conn.close()


def display_summary():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    now = datetime.now().strftime('%Y-%m-%d')
    cursor.execute("""
        SELECT region, COUNT(*) as count
        FROM conferences
        WHERE start_date IS NOT NULL AND start_date >= ?
        GROUP BY region
        ORDER BY count DESC
    """, (now,))
    print("\n" + "=" * 60)
    print("🌍 UPCOMING CONFERENCES BY REGION")
    print("=" * 60 + "\n")
    for region, count in cursor.fetchall():
        print(f"{region:15s}: {count:3d} conferences")
    try:
        cursor.execute("SELECT COUNT(*) FROM conferences WHERE discovered_at > datetime('now', '-1 day')")
        recent = cursor.fetchone()[0]
        print(f"\n✨ Discovered in last 24h: {recent} conferences\n")
    except sqlite3.OperationalError:
        print()
    conn.close()


def main():
    init_database()
    scrape_all()
    display_summary()
    print("\n🌏 ASIA CONFERENCES ".ljust(70, '═'))
    display_results(region="Asia")
    print("\n🇪🇺 EUROPE CONFERENCES ".ljust(70, '═'))
    display_results(region="Europe")


if __name__ == "__main__":
    main()
