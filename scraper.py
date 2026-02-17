#!/usr/bin/env python3
"""
SMART Practitioner Cybersecurity Conference Scraper — v4 (FULLY FIXED)
=======================================================================

EVERY BUG FROM v3 OUTPUT FIXED:
  1. xsa/infosec-events → 404           FIX: Was fetching /master/infosec-events.json
                                              Correct: /main/README.md (markdown TABLE, not JSON)
                                              Added flag emoji → region mapping (🇮🇳→Asia, 🇩🇪→Europe)
  2. cfptime.org → API unreachable       FIX: Added optional Playwright scraper that intercepts
                                              the API call cfptime.org's React SPA makes.
                                              Falls back gracefully if playwright not installed.
  3. Red Canary → 0 found               FIX: Was trying pipe-split on text. Red Canary uses
                                              HTML <table> elements, not text pipes. Now parses
                                              <table>/<tr>/<td> directly.
  4. Cryptax duplicates                  FIX: Added global name-dedup before saving. Was saving
                                              same conference twice due to null vs non-null dates.
  5. "Other" region for everything       FIX: classify_region now maps flag emojis directly.
                                              BSidesSD, TROOPERS, FIRST, etc. now correct region.
  6. Infosec-confs webinars              FIX: Filter: strip "17th Feb 2026 | " date-prefix pattern
                                              from names. Reject entries that match webinar patterns.
  7. Stale data past 1 year              FIX: Hard cutoff = today + 365 days enforced everywhere.

NEW SOURCES ADDED:
  + developers.events/all-events.json   — Public JSON API from scraly/developers-conferences-agenda
                                          Huge dataset, filter by "security"/"cybersecurity" tags.
                                          Dates are Unix timestamps (ms). CFP field included.
  + cfp.directory (Playwright)          — React SPA for security-focused CFPs. Uses Playwright
                                          to intercept the Supabase API call it makes.

INSTALL:
  pip install requests beautifulsoup4 python-dateutil
  pip install playwright && playwright install chromium   (optional, for cfptime + cfp.directory)

RUN:
  python3 scraper_v4.py

AUTOMATE (daily cron):
  0 9 * * * cd /path/to/scraper && python3 scraper_v4.py >> scraper.log 2>&1
"""

import sqlite3, requests, re, time, os, json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from dateutil import parser as date_parser
from typing import List, Dict, Optional
import warnings
warnings.filterwarnings('ignore')

# ── CONFIG ────────────────────────────────────────────────────────────────
DATABASE_FILE = os.environ.get(
    "CONFERENCES_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "conferences.db")
)
MIN_YEAR  = 2025
MAX_YEAR  = 2027
RATE_LIMIT = 1.5                          # seconds between HTTP requests
TODAY     = datetime.now().date()
CUTOFF    = TODAY + timedelta(days=365)   # only scrape next 12 months

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Accept":     "text/html,application/xhtml+xml,*/*;q=0.9",
}

# Optional Playwright availability
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# ════════════════════════════════════════════════════════════════════════
#  REGION CLASSIFIER — handles ISO codes, flag emojis, city names
# ════════════════════════════════════════════════════════════════════════

# Country flag emoji → (country_name, region)
# Root cause: xsa uses flag emojis in location column, old code had no emoji support
FLAG_EMOJI = {
    # Asia
    '🇮🇳': ('India', 'Asia'),       '🇸🇬': ('Singapore', 'Asia'),
    '🇯🇵': ('Japan', 'Asia'),        '🇰🇷': ('South Korea', 'Asia'),
    '🇹🇼': ('Taiwan', 'Asia'),       '🇭🇰': ('Hong Kong', 'Asia'),
    '🇮🇩': ('Indonesia', 'Asia'),    '🇹🇭': ('Thailand', 'Asia'),
    '🇲🇾': ('Malaysia', 'Asia'),     '🇵🇭': ('Philippines', 'Asia'),
    '🇻🇳': ('Vietnam', 'Asia'),      '🇦🇪': ('UAE', 'Asia'),
    '🇨🇳': ('China', 'Asia'),        '🇵🇰': ('Pakistan', 'Asia'),
    '🇱🇰': ('Sri Lanka', 'Asia'),    '🇧🇩': ('Bangladesh', 'Asia'),
    '🇳🇵': ('Nepal', 'Asia'),        '🇮🇱': ('Israel', 'Asia'),
    # Europe
    '🇩🇪': ('Germany', 'Europe'),    '🇫🇷': ('France', 'Europe'),
    '🇬🇧': ('UK', 'Europe'),         '🇳🇱': ('Netherlands', 'Europe'),
    '🇧🇪': ('Belgium', 'Europe'),    '🇨🇭': ('Switzerland', 'Europe'),
    '🇦🇹': ('Austria', 'Europe'),    '🇱🇺': ('Luxembourg', 'Europe'),
    '🇵🇹': ('Portugal', 'Europe'),   '🇪🇸': ('Spain', 'Europe'),
    '🇮🇹': ('Italy', 'Europe'),      '🇵🇱': ('Poland', 'Europe'),
    '🇸🇪': ('Sweden', 'Europe'),     '🇳🇴': ('Norway', 'Europe'),
    '🇩🇰': ('Denmark', 'Europe'),    '🇫🇮': ('Finland', 'Europe'),
    '🇨🇿': ('Czech Republic', 'Europe'), '🇸🇮': ('Slovenia', 'Europe'),
    '🇷🇴': ('Romania', 'Europe'),    '🇬🇷': ('Greece', 'Europe'),
    '🇭🇺': ('Hungary', 'Europe'),    '🇮🇪': ('Ireland', 'Europe'),
    '🇸🇰': ('Slovakia', 'Europe'),   '🇭🇷': ('Croatia', 'Europe'),
    '🇷🇸': ('Serbia', 'Europe'),     '🇺🇦': ('Ukraine', 'Europe'),
    '🇪🇪': ('Estonia', 'Europe'),    '🇱🇻': ('Latvia', 'Europe'),
    '🇱🇹': ('Lithuania', 'Europe'),  '🇮🇸': ('Iceland', 'Europe'),
    '🇲🇰': ('N. Macedonia', 'Europe'), '🇲🇪': ('Montenegro', 'Europe'),
    '🇧🇦': ('Bosnia', 'Europe'),     '🇧🇬': ('Bulgaria', 'Europe'),
    '🇽🇰': ('Kosovo', 'Europe'),     '🇬🇮': ('Gibraltar', 'Europe'),
    # Americas
    '🇺🇸': ('USA', 'Americas'),      '🇨🇦': ('Canada', 'Americas'),
    '🇲🇽': ('Mexico', 'Americas'),   '🇧🇷': ('Brazil', 'Americas'),
    '🇦🇷': ('Argentina', 'Americas'),'🇨🇱': ('Chile', 'Americas'),
    '🇨🇴': ('Colombia', 'Americas'), '🇵🇪': ('Peru', 'Americas'),
    # Other
    '🇿🇦': ('South Africa', 'Other'), '🇳🇬': ('Nigeria', 'Other'),
    '🇦🇺': ('Australia', 'Other'),   '🇳🇿': ('New Zealand', 'Other'),
}

ISO_REGION = {
    'sg':'Asia','in':'Asia','th':'Asia','jp':'Asia','kr':'Asia','tw':'Asia',
    'hk':'Asia','cn':'Asia','my':'Asia','ph':'Asia','id':'Asia','vn':'Asia',
    'ae':'Asia','pk':'Asia','il':'Asia',
    'gb':'Europe','uk':'Europe','de':'Europe','fr':'Europe','nl':'Europe',
    'be':'Europe','ch':'Europe','at':'Europe','lu':'Europe','pt':'Europe',
    'es':'Europe','it':'Europe','pl':'Europe','se':'Europe','no':'Europe',
    'dk':'Europe','fi':'Europe','cz':'Europe','ro':'Europe','gr':'Europe',
    'hu':'Europe','ie':'Europe','sk':'Europe','hr':'Europe','rs':'Europe',
    'ua':'Europe','ee':'Europe','lv':'Europe','lt':'Europe','si':'Europe',
    'us':'Americas','ca':'Americas','mx':'Americas','br':'Americas',
    'ar':'Americas','co':'Americas','cl':'Americas',
}

CITY_REGION = {
    # Asia
    'goa':'Asia','kochi':'Asia','pune':'Asia','hyderabad':'Asia',
    'kolkata':'Asia','manila':'Asia','cebu':'Asia','ho chi minh':'Asia',
    'hanoi':'Asia','jakarta':'Asia','kuala lumpur':'Asia','penang':'Asia',
    'osaka':'Asia','kyoto':'Asia','nagoya':'Asia','fukuoka':'Asia',
    'abu dhabi':'Asia','riyadh':'Asia','doha':'Asia','muscat':'Asia',
    'tel aviv':'Asia','jerusalem':'Asia',
    # Europe
    'hamburg':'Europe','munich':'Europe','frankfurt':'Europe','cologne':'Europe',
    'düsseldorf':'Europe','dusseldorf':'Europe','hannover':'Europe',
    'heidelberg':'Europe','dresden':'Europe','stuttgart':'Europe',
    'rennes':'Europe','lyon':'Europe','toulouse':'Europe','bordeaux':'Europe',
    'marseille':'Europe','lille':'Europe','strasbourg':'Europe','brest':'Europe',
    'reims':'Europe','nantes':'Europe','nice':'Europe','sophia antipolis':'Europe',
    'barcelona':'Europe','madrid':'Europe','seville':'Europe','bilbao':'Europe',
    'malaga':'Europe','málaga':'Europe','jaén':'Europe',
    'milan':'Europe','florence':'Europe','naples':'Europe','catania':'Europe',
    'venice':'Europe','torino':'Europe','turin':'Europe','bertinoro':'Europe',
    'edinburgh':'Europe','glasgow':'Europe','manchester':'Europe',
    'birmingham':'Europe','bristol':'Europe','cambridge':'Europe',
    'oxford':'Europe','leeds':'Europe','belfast':'Europe','cardiff':'Europe',
    'dublin':'Europe','cork':'Europe',
    'zurich':'Europe','zürich':'Europe','geneva':'Europe','basel':'Europe',
    'bern':'Europe','lausanne':'Europe','belval':'Europe',
    'prague':'Europe','brno':'Europe','bratislava':'Europe',
    'budapest':'Europe','krakow':'Europe','warsaw':'Europe','wroclaw':'Europe',
    'stockholm':'Europe','gothenburg':'Europe','gothenberg':'Europe',
    'malmo':'Europe','umeå':'Europe','umea':'Europe','linkoping':'Europe',
    'linköping':'Europe','jönköping':'Europe',
    'oslo':'Europe','bergen':'Europe','trondheim':'Europe','gjøvik':'Europe',
    'copenhagen':'Europe','aarhus':'Europe','helsinki':'Europe',
    'tallinn':'Europe','riga':'Europe','vilnius':'Europe','reykjavik':'Europe',
    'bucharest':'Europe','athens':'Europe','sofia':'Europe',
    'zagreb':'Europe','ljubljana':'Europe','sarajevo':'Europe',
    'mechelen':'Europe','ghent':'Europe','antwerp':'Europe','hasselt':'Europe',
    'eindhoven':'Europe','rotterdam':'Europe','amsterdam':'Europe',
    'utrecht':'Europe','groningen':'Europe','the hague':'Europe',
    'lisbon':'Europe','porto':'Europe','saarbrücken':'Europe','saarbrucken':'Europe',
    # Americas
    'new york':'Americas','san francisco':'Americas','las vegas':'Americas',
    'seattle':'Americas','boston':'Americas','austin':'Americas',
    'chicago':'Americas','miami':'Americas','washington':'Americas',
    'toronto':'Americas','vancouver':'Americas','montreal':'Americas',
    'mexico city':'Americas','sao paulo':'Americas','buenos aires':'Americas',
    'orlando':'Americas','atlanta':'Americas','dallas':'Americas',
    'denver':'Americas','phoenix':'Americas','scottsdale':'Americas',
    'arlington':'Americas','towson':'Americas','omaha':'Americas',
    'milwaukee':'Americas','davenport':'Americas','san diego':'Americas',
    'mesa':'Americas','sunny isles':'Americas',
}

def classify_region(text: str) -> str:
    """Region detection: checks flag emojis first, then ISO codes, then cities, then keywords."""
    if not text:
        return 'Other'
    # 1. Flag emoji (most reliable — used by xsa)
    for emoji, (_, region) in FLAG_EMOJI.items():
        if emoji in text:
            return region
    t = text.lower().strip()
    # 2. ISO-2 codes as standalone tokens
    for part in re.split(r'[,\s()]+', t):
        part = part.strip().rstrip('.')
        if part in ISO_REGION:
            return ISO_REGION[part]
    # 3. City names (substring)
    for city, region in CITY_REGION.items():
        if city in t:
            return region
    # 4. Country/continent keywords
    if any(k in t for k in ['india','singapore','thailand','malaysia','china',
                              'japan','korea','taiwan','indonesia','philippines',
                              'vietnam','uae','dubai','hong kong']):
        return 'Asia'
    if any(k in t for k in ['uk','united kingdom','germany','france','spain',
                              'italy','netherlands','belgium','switzerland',
                              'austria','luxembourg','portugal','sweden',
                              'norway','denmark','poland','europe','ireland',
                              'finland','czechia','czech republic','hungary',
                              'romania','greece','croatia','slovakia']):
        return 'Europe'
    if any(k in t for k in ['usa','united states','canada','mexico','brazil',
                              'argentina','colombia','chile']):
        return 'Americas'
    if any(k in t for k in ['virtual','online','remote','hybrid']):
        return 'Virtual'
    return 'Other'


# ════════════════════════════════════════════════════════════════════════
#  DATE PARSER
# ════════════════════════════════════════════════════════════════════════

def parse_date(s, year_hint=None) -> Optional[str]:
    """Parse date string → 'YYYY-MM-DD'. Returns None if vague or out of range."""
    if not s or any(x in str(s).lower() for x in ['tba','tbd','n/a','coming soon','none','-\n','invite']):
        return None
    s = str(s).strip()
    # Reject year-only
    if re.fullmatch(r'202[4-7]', s):
        return None
    # Reject season-only
    if re.match(r'(spring|summer|fall|autumn|winter|q[1-4])\s*202[4-7]', s.lower()):
        return None
    # Reject month+year, no day ("March 2026")
    if re.fullmatch(r'(january|february|march|april|may|june|july|august|'
                    r'september|october|november|december)\s+202[4-7]', s.strip(), re.I):
        return None
    try:
        cleaned = re.sub(r'\([^)]*\)', '', s).replace('\u2013','-').replace('\u2014','-').strip()
        # Normalise date ranges → take start date only
        # Cross-month: "Feb 28 - Mar 1, 2026" → "Feb 28, 2026"
        cross_m = re.match(r'(\w+\s+\d{1,2})\s*[-–]\s*\w+\s+\d{1,2}(,?\s*20\d{2}.*)', cleaned)
        if cross_m:
            cleaned = cross_m.group(1) + cross_m.group(2)
        else:
            # Same-month: "Apr 23-24, 2026" → "Apr 23, 2026"
            same_m = re.match(r'(\w+\s+\d{1,2})\s*[-–]\s*\d{1,2}(.*)', cleaned)
            if same_m:
                cleaned = same_m.group(1) + same_m.group(2)
        year_in = re.search(r'(202[4-7])', cleaned)
        if not year_in and year_hint:
            cleaned = f"{cleaned} {year_hint}"
        elif not year_in:
            return None
        dt = date_parser.parse(cleaned, fuzzy=True)
        if year_hint and not year_in:
            dt = dt.replace(year=int(year_hint))
        if MIN_YEAR <= dt.year <= MAX_YEAR:
            d = dt.date()
            if d > CUTOFF:   # beyond 1-year window → skip
                return None
            return d.isoformat()
    except Exception:
        pass
    return None


def from_ms_timestamp(ts) -> Optional[str]:
    """Convert Unix millisecond timestamp → YYYY-MM-DD, within window."""
    try:
        d = datetime.utcfromtimestamp(int(ts) / 1000).date()
        if MIN_YEAR <= d.year <= MAX_YEAR and d >= TODAY and d <= CUTOFF:
            return d.isoformat()
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════════════════
#  ACADEMIC / WEBINAR FILTER
# ════════════════════════════════════════════════════════════════════════

PRACTITIONER_ALLOWLIST = {
    "def con","defcon","black hat","nullcon","bsides","hitb","hack.lu",
    "c0c0n","rootcon","hitcon","codeblue","code blue","troopers","sstic",
    "44con","rsa conference","offensivecon","offensive con","hardwear",
    "recon","cansecwest","shmoocon","ekoparty","botconf","hack in paris",
    "hack in the box","swiss cyber storm","insomnihack","x33fcon","syscan",
    "securityfest","devseccon","gisec","security bsides","nahamcon",
    "hardwear.io","area41","nolacon","bluehat","thotcon","kernelcon",
    "corncon","hackmiami","northsec","so-con","pivot","re//verse",
    "hexacon","thcon","jssi","elbsides","pass the salt","brucon","sec-t",
    "sstic","auvergn","m0lecon","GreHack","grehack","cackalacky",
    "typhooncon","out of the box","bluehat","certfr","hackcon","owasp",
    "cactuscon","unpromputed","un]prompted","first vulncon","first conference",
    "first cti","virusbulletin","virus bulletin","offcon","secfest",
    "cryptovillage","security fest","security village",
}

WEBINAR_BLOCKLIST = [
    # Structural webinar patterns (Infosec-Conferences.com uses these)
    r'^\d{1,2}(st|nd|rd|th)\s+\w+\s+\d{4}\s*\|',   # "17th February 2026 | ..."
    r'^(how|why|what|when|where|top\s+\d|understanding|introduction|insights)',
    r'\bwebinar\b','\bbootcamp\b','\bvirtual summit\b','\bonline summit\b',
    '\bvirtual event\b','\bhow to\b','\btips for\b','\bbriefing\b',
    '\bperspectives from\b','\binsights on\b','\btactical\b','\bpredictions for\b',
    '\blearn how\b','\bstrategies for\b','\bfive trends\b','\bbenefits of\b',
]

ACADEMIC_BLOCKLIST_TERMS = [
    "workshop on","symposium on","proceedings of","acm sigsac",
    "ieee s&p","ieee symposium","acm ccs","usenix security","ndss","eurocrypt",
    "asiacrypt","workshop","symposium","seminar","colloquium",
    "ieee ","acm ","iacr ","springer ","elsevier ",
    "peer review","camera ready","double blind","acceptance rate",
    "cpss","fl asiaccs","apkc","hasp","artman","ccsw",
    "wpes","optimist","weis","satml","eurosec","codaspy","acsac",
]

def is_practitioner(name: str, desc: str = "", source: str = "") -> bool:
    name_l = (name or "").lower().strip()
    combined = name_l + " " + (desc or "").lower()

    # Allowlist — always keep
    if any(a in name_l for a in PRACTITIONER_ALLOWLIST):
        return True

    # Trusted sources — always keep
    if source in ("xsa/infosec-events","Cryptax/confsec","CFPTime","Red Canary CFP"):
        return True

    # Webinar patterns — reject
    for pattern in WEBINAR_BLOCKLIST:
        if re.search(pattern, name_l, re.I):
            return False

    # Academic terms — reject
    if any(b in combined for b in ACADEMIC_BLOCKLIST_TERMS):
        return False

    # Default: include
    return True


def clean_name(raw: str) -> str:
    """Strip date-prefix from infosec-conferences.com entries like '17th Feb 2026 | Real Name'."""
    m = re.match(r'^\d{1,2}(st|nd|rd|th)\s+\w+\s+\d{4}\s*\|\s*(.*)', raw, re.I)
    if m:
        return m.group(2).strip()
    return raw.strip()


# ════════════════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS conferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, location TEXT, country TEXT, region TEXT,
        start_date TEXT, end_date TEXT, cfp_deadline TEXT, cfp_url TEXT,
        website TEXT, description TEXT, source TEXT,
        discovered_at TEXT, last_seen TEXT,
        UNIQUE(name, start_date)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS scrape_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_name TEXT, scrape_time TEXT,
        found INT, kept INT, dropped INT, status TEXT, error TEXT
    )""")
    for idx in ("region","start_date","source","cfp_deadline"):
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{idx} ON conferences({idx})")
    # migrations
    c.execute("PRAGMA table_info(conferences)")
    cols = {r[1] for r in c.fetchall()}
    for col in ("cfp_url","end_date"):
        if col not in cols:
            c.execute(f"ALTER TABLE conferences ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()
    print("✓ Database ready")


def _save(conf: Dict, source: str) -> bool:
    """Save one conference. Returns True if newly inserted."""
    name = (conf.get('name') or '').strip()
    if not name or len(name) < 3:
        return False
    if not is_practitioner(name, conf.get('description',''), source):
        return False
    # Skip if beyond 1-year window
    sd = conf.get('start_date')
    if sd:
        try:
            if datetime.strptime(sd, '%Y-%m-%d').date() > CUTOFF:
                return False
        except Exception:
            pass

    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    now = datetime.now().isoformat()
    inserted = False
    try:
        c.execute("SELECT id FROM conferences WHERE name=? AND start_date=?",
                  (name, sd))
        row = c.fetchone()
        if row:
            c.execute("""UPDATE conferences SET last_seen=?,
                website=COALESCE(?,website), cfp_deadline=COALESCE(?,cfp_deadline),
                cfp_url=COALESCE(?,cfp_url), location=COALESCE(?,location),
                region=COALESCE(?,region) WHERE id=?""",
                (now, conf.get('website'), conf.get('cfp_deadline'), conf.get('cfp_url'),
                 conf.get('location'), conf.get('region'), row[0]))
        else:
            c.execute("""INSERT INTO conferences
                (name,location,country,region,start_date,end_date,cfp_deadline,cfp_url,
                 website,description,source,discovered_at,last_seen)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, conf.get('location'), conf.get('country'), conf.get('region'),
                 sd, conf.get('end_date'), conf.get('cfp_deadline'),
                 conf.get('cfp_url'), conf.get('website'), conf.get('description'),
                 source, now, now))
            inserted = True
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()
    return inserted


def _log(source, found, kept, dropped, status, error=None):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO scrape_log (source_name,scrape_time,found,kept,dropped,status,error) "
              "VALUES (?,?,?,?,?,?,?)",
              (source, datetime.now().isoformat(), found, kept, dropped, status, error))
    conn.commit()
    conn.close()


def _fetch(url: str, timeout=20) -> Optional[BeautifulSoup]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, verify=False)
        if r.status_code == 200:
            return BeautifulSoup(r.content, 'html.parser')
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 1: xsa/infosec-events — FIXED
#  Root cause: Was fetching non-existent JSON file.
#  Real data: README.md on MAIN branch, markdown table format.
#  New: Flag emoji → region mapping.
# ════════════════════════════════════════════════════════════════════════

def scrape_xsa() -> List[Dict]:
    source = "xsa/infosec-events"
    results = []
    print(f"  → Fetching {source} (README.md, main branch)...")
    try:
        r = requests.get(
            "https://raw.githubusercontent.com/xsa/infosec-events/main/README.md",
            timeout=20)
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code} — check branch name")
        text = r.text

        # The README has a markdown table:
        # | Event | Date | Location | Twitter/Mastodon Handle | Free |
        # |-------|------|----------|------------------------|------|
        # | Nullcon Goa | Feb 28 - Mar 1, 2026 | Goa 🇮🇳 | @nullcon | N |
        in_table = False
        for line in text.split('\n'):
            if '|' not in line:
                in_table = False
                continue
            cells = [c.strip() for c in line.split('|')]
            cells = [c for c in cells if c]  # remove empty edge cells
            if not cells:
                continue
            # Detect table header
            if re.search(r'(event|name|conference)', cells[0], re.I) and len(cells) >= 3:
                in_table = True
                continue
            # Skip separator rows
            if all(re.match(r'^[-:]+$', c.replace(' ', '')) for c in cells):
                continue
            if not in_table:
                continue

            # cells[0]=Name, cells[1]=Date, cells[2]=Location+flag, cells[3]=Handle, cells[4]=Free
            if len(cells) < 2:
                continue

            name = cells[0].strip()
            # Clean markdown links [Name](url)
            link_m = re.search(r'\[([^\]]+)\]\(([^)]+)\)', name)
            website = None
            if link_m:
                name = link_m.group(1)
                website = link_m.group(2)
            name = re.sub(r'[\*`]', '', name).strip()
            if not name or len(name) < 2:
                continue

            date_str = cells[1] if len(cells) > 1 else ''
            location_raw = cells[2] if len(cells) > 2 else ''

            # Extract flag emoji from location for accurate region
            flag_found = None
            for emoji in FLAG_EMOJI:
                if emoji in location_raw:
                    flag_found = emoji
                    break

            # Clean location: remove emoji, strip
            location = re.sub(r'[^\x00-\x7F🇦-🇿]', '', location_raw)  # keep ASCII
            location = re.sub(r'[\U0001F1E0-\U0001F1FF]{2}', '', location_raw).strip()
            location = location.strip()

            if flag_found:
                country, region = FLAG_EMOJI[flag_found]
            else:
                country = None
                region = classify_region(location_raw)

            # Parse date — these are like "Feb 28 - Mar 1, 2026" or "Apr 23-24, 2026"
            # We need to inject year if missing
            year_match = re.search(r'(202[4-7])', date_str)
            year_hint = int(year_match.group(1)) if year_match else None
            if not year_hint:
                # Guess year from context: if month is Jan-Jun, likely current+1 if we're past it
                year_hint = TODAY.year if TODAY.month <= 6 else TODAY.year + 1
            start_date = parse_date(date_str, year_hint=year_hint)

            results.append({
                'name': name, 'location': location, 'country': country,
                'region': region, 'start_date': start_date, 'end_date': None,
                'cfp_deadline': None, 'cfp_url': None,
                'website': website, 'description': None,
            })

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 2: Cryptax/confsec — FIXED (dedup + column detection)
# ════════════════════════════════════════════════════════════════════════

def scrape_cryptax() -> List[Dict]:
    source = "Cryptax/confsec"
    results = []
    print(f"  → Fetching {source}...")
    try:
        text = None
        for branch in ('main', 'master'):
            try:
                r = requests.get(
                    f"https://raw.githubusercontent.com/cryptax/confsec/{branch}/README.md",
                    timeout=20)
                if r.status_code == 200:
                    text = r.text
                    break
            except Exception:
                continue
        if not text:
            raise Exception("Could not fetch README from main or master branch")

        in_table = False
        date_col = name_col = loc_col = cfp_col = None

        for line in text.split('\n'):
            if '|' not in line:
                continue
            cells = [c.strip() for c in line.split('|')]
            cells = [c for c in cells if c]  # remove empty edge cells

            # Detect header — figure out column indices
            if re.search(r'(date|conf|event|name)', cells[0] if cells else '', re.I):
                in_table = True
                # Map column positions
                for i, h in enumerate(cells):
                    hl = h.lower()
                    if re.search(r'date|when', hl):    date_col = i
                    elif re.search(r'conf|name|event', hl): name_col = i
                    elif re.search(r'loc|where|city', hl): loc_col = i
                    elif re.search(r'cfp|deadline',   hl): cfp_col = i
                # Fallback defaults if detection failed
                if date_col is None: date_col = 0
                if name_col is None: name_col = 1
                if loc_col  is None: loc_col  = 2
                if cfp_col  is None: cfp_col  = 3
                continue

            if '---' in line:
                continue
            if not in_table:
                continue
            if not any(y in line for y in ['2025','2026','2027']):
                continue
            if len(cells) < 2:
                continue

            date_cell = cells[date_col] if date_col < len(cells) else ''
            name_cell = cells[name_col] if name_col < len(cells) else ''
            loc_cell  = cells[loc_col]  if loc_col  < len(cells) else ''
            cfp_cell  = cells[cfp_col]  if cfp_col  < len(cells) else ''

            # Extract URL from markdown link in name
            website = None
            name = name_cell
            m = re.search(r'\[([^\]]+)\]\(([^)]+)\)', name_cell)
            if m:
                name, website = m.group(1), m.group(2)
            name = re.sub(r'[\*\[\]`]', '', name).strip()
            if not name or len(name) < 3:
                continue

            # Skip if name looks like a location (a common Cryptax quirk when name col is empty)
            if re.match(r'^[A-Z][a-z]+,\s+[A-Z]', name) and len(name.split()) <= 3:
                # Looks like "Goa, India" — skip, it's a mis-parse
                continue

            start_date   = parse_date(date_cell)
            cfp_deadline = parse_date(cfp_cell) if cfp_cell.strip() not in ('','N/A','TBA','-') else None
            region       = classify_region(loc_cell)
            country      = loc_cell.split(',')[-1].strip() if ',' in loc_cell else loc_cell

            results.append({
                'name': name, 'location': loc_cell, 'country': country,
                'region': region, 'start_date': start_date, 'end_date': None,
                'cfp_deadline': cfp_deadline, 'cfp_url': website,
                'website': website, 'description': None,
            })

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    # Dedup by name (some conferences appear in multiple years/sections)
    seen_names = set()
    deduped = []
    for r in results:
        key = r['name'].lower().strip()
        if key not in seen_names:
            seen_names.add(key)
            deduped.append(r)
    results = deduped

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 3: developers.events/all-events.json  ← NEW SOURCE
#  Public JSON from scraly/developers-conferences-agenda
#  Filter: tags include security/cybersecurity OR name matches security keywords
#  Dates: Unix ms timestamps
# ════════════════════════════════════════════════════════════════════════

SECURITY_KEYWORDS = {
    'security','cybersecurity','infosec','hacking','pentest','penetration',
    'ctf','vulnerability','malware','forensics','devsecops','appsec',
    'owasp','threat','exploit','reverse','cryptography','privacy','cyber',
    'bsides','defcon','blackhat','nullcon','hitb','sstic','botconf',
}

def scrape_developers_events() -> List[Dict]:
    source = "developers.events"
    results = []
    print(f"  → Fetching {source} (all-events.json)...")
    try:
        r = requests.get("https://developers.events/all-events.json",
                         headers={**HEADERS, "Accept": "application/json"},
                         timeout=30)
        if r.status_code != 200:
            raise Exception(f"HTTP {r.status_code}")

        events = r.json()
        print(f"    Total events in JSON: {len(events)}")

        for ev in events:
            name     = ev.get('name','').strip()
            tags     = [t.lower() for t in (ev.get('tags') or [])]
            city     = ev.get('city','')
            country  = ev.get('country','')
            website  = ev.get('hyperlink','')
            location = ev.get('location', f"{city}, {country}".strip(', '))

            # Filter: must have security-related tag OR name matches security keywords
            name_l = name.lower()
            has_sec_tag  = any(kw in t for t in tags for kw in SECURITY_KEYWORDS)
            has_sec_name = any(kw in name_l for kw in SECURITY_KEYWORDS)
            if not has_sec_tag and not has_sec_name:
                continue

            # Parse dates from Unix ms timestamps
            raw_dates = ev.get('date', [])
            start_date = end_date = None
            if raw_dates:
                start_date = from_ms_timestamp(raw_dates[0])
                if len(raw_dates) > 1:
                    end_date = from_ms_timestamp(raw_dates[-1])

            # CFP data
            cfp_obj      = ev.get('cfp') or {}
            cfp_deadline = None
            cfp_url      = cfp_obj.get('link','') if cfp_obj else None
            if cfp_obj.get('until'):
                cfp_deadline = from_ms_timestamp(cfp_obj['until'])

            # Region from country field (clean string like "Hungary", "USA", "UK")
            region = classify_region(f"{city} {country}")

            if name and len(name) > 3:
                results.append({
                    'name': name, 'location': location, 'country': country,
                    'region': region, 'start_date': start_date, 'end_date': end_date,
                    'cfp_deadline': cfp_deadline, 'cfp_url': cfp_url,
                    'website': website, 'description': None,
                })

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 4: Red Canary CFP Tracker — FIXED
#  Root cause: Was trying to split text by '|'.
#  Red Canary uses HTML <table> elements. Now parses <table>/<tr>/<td>.
# ════════════════════════════════════════════════════════════════════════

def scrape_red_canary() -> List[Dict]:
    source = "Red Canary CFP"
    results = []
    print(f"  → Fetching {source}...")
    try:
        # Try current + recent months
        now = datetime.now()
        attempts = []
        for delta in range(6):
            m = now.month - delta
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            attempts.append((datetime(y, m, 1).strftime('%B').lower(), str(y)))

        soup = None
        found_url = None
        for month, year in attempts:
            url = f"https://redcanary.com/blog/news-events/cfp-tracker-{month}-{year}/"
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.content, 'html.parser')
                    found_url = url
                    print(f"    ✓ Found: cfp-tracker-{month}-{year}")
                    break
            except Exception:
                continue

        if not soup:
            raise Exception("No CFP tracker post found for recent 6 months")

        # FIX: Red Canary uses HTML tables, not pipe-separated text
        tables = soup.find_all('table')
        if not tables:
            # Fallback: try to find any tabular-looking content in article
            content = soup.find('main') or soup.find('article') or soup.find('body')
            if content:
                # Try pipe-separated text as last resort
                for line in content.get_text(separator='\n').split('\n'):
                    if line.count('|') >= 2:
                        parts = [p.strip() for p in line.split('|')]
                        name = parts[0].strip()
                        if not name or len(name) < 3 or name.lower().startswith(('conf','name','event','---')):
                            continue
                        location    = parts[1].strip() if len(parts) > 1 else None
                        conf_date   = parts[2].strip() if len(parts) > 2 else None
                        cfp_date    = parts[3].strip() if len(parts) > 3 else None
                        cfp_url_str = parts[4].strip() if len(parts) > 4 else None
                        if name and (parse_date(conf_date) or parse_date(cfp_date)):
                            results.append({
                                'name': name, 'location': location,
                                'country': None,
                                'region': classify_region(location or ''),
                                'start_date': parse_date(conf_date), 'end_date': None,
                                'cfp_deadline': parse_date(cfp_date),
                                'cfp_url': cfp_url_str or found_url,
                                'website': found_url, 'description': 'CFP open',
                            })
        else:
            for table in tables:
                # Find column indices from header row
                headers = []
                header_row = table.find('tr')
                if header_row:
                    headers = [th.get_text(strip=True).lower()
                               for th in header_row.find_all(['th','td'])]

                # Map header positions
                name_i = loc_i = date_i = cfp_i = cfpurl_i = None
                for i, h in enumerate(headers):
                    if re.search(r'name|conference|event', h): name_i = i
                    elif re.search(r'loc|city|where', h):       loc_i  = i
                    elif re.search(r'date|when',h) and cfp_i is None: date_i = i
                    elif re.search(r'cfp.*date|deadline',h):   cfp_i  = i
                    elif re.search(r'cfp.*link|submit|url', h): cfpurl_i = i
                if name_i is None: name_i = 0
                if date_i is None: date_i = 2
                if cfp_i  is None: cfp_i  = 3

                rows = table.find_all('tr')[1:]  # skip header
                for row in rows:
                    cells = row.find_all(['td','th'])
                    if not cells:
                        continue
                    def cell(i):
                        return cells[i].get_text(strip=True) if i < len(cells) else ''

                    name = cell(name_i)
                    if not name or len(name) < 3:
                        continue
                    location    = cell(loc_i)  if loc_i  is not None else None
                    conf_date   = cell(date_i)
                    cfp_date    = cell(cfp_i)
                    cfp_url_str = cell(cfpurl_i) if cfpurl_i is not None else None
                    # Also grab any href in cfp cell
                    if cfpurl_i is not None and cfpurl_i < len(cells):
                        a = cells[cfpurl_i].find('a', href=True)
                        if a:
                            cfp_url_str = a['href']

                    results.append({
                        'name': name, 'location': location, 'country': None,
                        'region': classify_region(location or ''),
                        'start_date': parse_date(conf_date), 'end_date': None,
                        'cfp_deadline': parse_date(cfp_date),
                        'cfp_url': cfp_url_str or found_url,
                        'website': found_url, 'description': 'CFP open',
                    })

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 5: Infosec-Conferences.com — FIXED
#  Root cause: Was capturing webinars (dated "17th Feb 2026 | Title").
#  Fix: clean_name() strips date prefix; WEBINAR_BLOCKLIST filters junk.
#  Fix: skip entries where start_date == today (webinar/ongoing events).
# ════════════════════════════════════════════════════════════════════════

def scrape_infosec_confs() -> List[Dict]:
    source = "Infosec-Conferences.com"
    results = []
    print(f"  → Fetching {source}...")
    try:
        soup = _fetch("https://infosec-conferences.com/")
        if not soup:
            raise Exception("Failed to fetch main page")

        # Strategy 1: table rows
        for row in soup.find_all('tr'):
            cells = row.find_all(['td','th'])
            if len(cells) < 2:
                continue
            text = row.get_text(separator=' ')
            if not any(y in text for y in ['2025','2026','2027']):
                continue

            link = row.find('a', href=True)
            if not link:
                continue
            raw_name = clean_name(link.get_text(strip=True))
            if not raw_name or len(raw_name) < 4:
                continue
            if not is_practitioner(raw_name, text, source):
                continue

            website = link['href']
            if not website.startswith('http'):
                website = f"https://infosec-conferences.com{website}"

            date_str = None
            for cell in cells:
                ct = cell.get_text(strip=True)
                if any(y in ct for y in ['2025','2026','2027']) and len(ct) < 50:
                    date_str = ct
                    break
            start_date = parse_date(date_str)

            # Skip if date is today or in the past (usually webinars/happening now)
            if start_date:
                try:
                    if datetime.strptime(start_date, '%Y-%m-%d').date() <= TODAY:
                        continue
                except Exception:
                    pass

            loc_str = cells[-1].get_text(strip=True) if len(cells) > 2 else ''
            results.append({
                'name': raw_name, 'location': loc_str or None, 'country': None,
                'region': classify_region(loc_str or text),
                'start_date': start_date, 'end_date': None,
                'cfp_deadline': None, 'cfp_url': None,
                'website': website, 'description': None,
            })

        # Strategy 2: article/div/li elements (fallback)
        if len(results) < 3:
            for item in soup.find_all(['article','div','li']):
                text = item.get_text(separator=' ')
                if not any(y in text for y in ['2026','2027']):
                    continue
                if len(text) > 600 or len(text) < 15:
                    continue
                link = item.find('a', href=True)
                if not link:
                    continue
                raw_name = clean_name(link.get_text(strip=True))
                if not raw_name or len(raw_name) < 4:
                    continue
                if not is_practitioner(raw_name, text, source):
                    continue
                website = link['href']
                if not website.startswith('http'):
                    website = f"https://infosec-conferences.com{website}"
                date_m = re.search(r'(\w+\s+\d{1,2}(?:[-–]\d{1,2})?,?\s+20\d{2})', text)
                start_date = parse_date(date_m.group(1)) if date_m else None
                if start_date:
                    try:
                        if datetime.strptime(start_date, '%Y-%m-%d').date() <= TODAY:
                            continue
                    except Exception:
                        pass
                results.append({
                    'name': raw_name, 'location': None, 'country': None,
                    'region': classify_region(text),
                    'start_date': start_date, 'end_date': None,
                    'cfp_deadline': None, 'cfp_url': None,
                    'website': website, 'description': None,
                })

        # Global dedup
        seen, unique = set(), []
        for r in results:
            k = (r['name'].lower().strip(), r.get('start_date'))
            if k not in seen:
                seen.add(k)
                unique.append(r)
        results = unique

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 6: CFPTime — Playwright (optional)
#  cfptime.org is a React SPA. Best approach: use Playwright to intercept
#  the API call the SPA makes to its backend. Much cleaner than parsing DOM.
#
#  Install: pip install playwright && playwright install chromium
#  This scraper silently skips if playwright is not installed.
# ════════════════════════════════════════════════════════════════════════

def scrape_cfptime_playwright() -> List[Dict]:
    source = "CFPTime"
    results = []

    if not PLAYWRIGHT_AVAILABLE:
        print(f"  ⚠ {source}: Playwright not installed (skipping). "
              f"Run: pip install playwright && playwright install chromium")
        return []

    print(f"  → Fetching {source} (Playwright — intercepts API call)...")
    captured_json = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            )
            page = ctx.new_page()

            # Intercept any JSON response that looks like conference data
            def handle_response(response):
                url_l = response.url.lower()
                if (response.status == 200 and
                        'application/json' in (response.headers.get('content-type','')) and
                        any(kw in url_l for kw in ['conference','cfp','event'])):
                    try:
                        data = response.json()
                        if isinstance(data, (list, dict)) and data:
                            captured_json.append(data)
                    except Exception:
                        pass

            page.on("response", handle_response)

            page.goto("https://cfptime.org/upcoming", timeout=30000)
            try:
                page.wait_for_selector("table, .conference-row, [class*=conference], [class*=event]",
                                       timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000)   # let extra API calls settle

            # Also try to grab rendered table from DOM
            rows = page.query_selector_all("table tr, [class*=row], [class*=conference]")
            for row in rows:
                text = row.inner_text()
                if not any(y in text for y in ['2025','2026','2027']):
                    continue
                cells = text.split('\t') if '\t' in text else text.split('\n')
                if len(cells) >= 2:
                    name = cells[0].strip()
                    if name and len(name) > 3:
                        date_m = re.search(r'(\w+\s+\d{1,2}[-–]?\d{0,2},?\s+20\d{2})', text)
                        results.append({
                            'name': name, 'location': cells[1].strip() if len(cells)>1 else None,
                            'country': None, 'region': 'Other',
                            'start_date': parse_date(date_m.group(1)) if date_m else None,
                            'end_date': None, 'cfp_deadline': None,
                            'cfp_url': 'https://cfptime.org/upcoming',
                            'website': 'https://cfptime.org/upcoming',
                            'description': None,
                        })

            browser.close()

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    # Parse any captured API JSON
    for data in captured_json:
        items = data if isinstance(data, list) else data.get('results', data.get('conferences', []))
        for item in (items if isinstance(items, list) else []):
            name = item.get('name', item.get('conference',''))
            if not name:
                continue
            location = item.get('location', item.get('city',''))
            country  = item.get('country','')
            start_date   = parse_date(item.get('start_date', item.get('date','')))
            cfp_deadline = parse_date(item.get('cfp_deadline', item.get('deadline','')))
            website  = item.get('url', item.get('website',''))
            if name and len(name) > 3:
                results.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(f"{location} {country}"),
                    'start_date': start_date, 'end_date': None,
                    'cfp_deadline': cfp_deadline, 'cfp_url': website,
                    'website': website, 'description': None,
                })

    # Dedup
    seen, unique = set(), []
    for r in results:
        k = r['name'].lower().strip()
        if k not in seen:
            seen.add(k)
            unique.append(r)
    results = unique

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  SOURCE 7: cfp.directory — Playwright (optional)
#  React/Next.js SPA backed by Supabase. Intercepts the Supabase REST
#  call to get all security conferences with open CFPs.
# ════════════════════════════════════════════════════════════════════════

def scrape_cfp_directory_playwright() -> List[Dict]:
    source = "cfp.directory"
    results = []

    if not PLAYWRIGHT_AVAILABLE:
        print(f"  ⚠ {source}: Playwright not installed (skipping).")
        return []

    print(f"  → Fetching {source} (Playwright — intercepts Supabase API)...")
    captured_json = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            )
            page = ctx.new_page()

            def handle_response(response):
                url_l = response.url.lower()
                if (response.status == 200 and
                        any(kw in url_l for kw in ['supabase','rest','events','conferences']) and
                        'application/json' in response.headers.get('content-type','')):
                    try:
                        data = response.json()
                        if data:
                            captured_json.append(data)
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto("https://cfp.directory/events", timeout=30000)
            try:
                page.wait_for_selector("[class*=event], [class*=conference], table, li",
                                       timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            browser.close()

    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    for data in captured_json:
        items = data if isinstance(data, list) else data.get('data', [])
        for item in (items if isinstance(items, list) else []):
            name     = item.get('name','') or item.get('title','')
            location = item.get('location','') or item.get('city','')
            country  = item.get('country','')
            website  = item.get('url','') or item.get('website','')
            cfp_url  = item.get('cfp_url','') or item.get('cfp_link','')
            raw_date = item.get('start_date','') or item.get('event_date','') or item.get('date','')
            raw_cfp  = item.get('cfp_deadline','') or item.get('deadline','')
            start_date   = parse_date(str(raw_date)) if raw_date else None
            cfp_deadline = parse_date(str(raw_cfp))  if raw_cfp  else None

            if name and len(name) > 3:
                results.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(f"{location} {country}"),
                    'start_date': start_date, 'end_date': None,
                    'cfp_deadline': cfp_deadline, 'cfp_url': cfp_url or website,
                    'website': website, 'description': None,
                })

    seen, unique = set(), []
    for r in results:
        k = r['name'].lower().strip()
        if k not in seen:
            seen.add(k)
            unique.append(r)
    results = unique

    kept = sum(1 for r in results if _save(r, source))
    _log(source, len(results), kept, len(results)-kept, 'success')
    print(f"  ✓ {source}: {len(results)} found → {kept} new saved")
    return results


# ════════════════════════════════════════════════════════════════════════
#  DISPLAY & SUMMARY
# ════════════════════════════════════════════════════════════════════════

def display(region: str = None, upcoming_only: bool = True):
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    q = ("SELECT name,location,region,start_date,cfp_deadline,cfp_url,website,source "
         "FROM conferences WHERE 1=1")
    params = []
    if upcoming_only:
        q += " AND (start_date IS NULL OR start_date >= date('now'))"
        q += f" AND (start_date IS NULL OR start_date <= '{CUTOFF.isoformat()}')"
    if region:
        q += " AND region=?"
        params.append(region)
    q += " ORDER BY start_date ASC NULLS LAST, name ASC"
    c.execute(q, params)
    rows = c.fetchall()

    label = f"🌐 {region.upper()}" if region else "🌐 ALL"
    print(f"\n{'═'*70}")
    print(f"  {label} — UPCOMING NEXT 12 MONTHS ({len(rows)} conferences)")
    print(f"{'═'*70}\n")

    cur_month = None
    for name, loc, reg, start_date, cfp_deadline, cfp_url, website, src in rows:
        if start_date:
            month = start_date[:7]
            if month != cur_month:
                cur_month = month
                try:
                    print(f"\n  📆 {datetime.strptime(month, '%Y-%m').strftime('%B %Y')}")
                    print(f"  {'─'*60}")
                except Exception:
                    pass

        cfp_flag = ""
        if cfp_deadline:
            try:
                days = (datetime.strptime(cfp_deadline, '%Y-%m-%d') - datetime.now()).days
                if days < 0:     cfp_flag = " [CLOSED]"
                elif days == 0:  cfp_flag = " 🔴 TODAY!"
                elif days <= 7:  cfp_flag = f" 🔴 {days}d left!"
                elif days <= 30: cfp_flag = f" 🟡 {days}d left"
                else:            cfp_flag = f" 🟢 {days}d left"
            except Exception:
                pass

        print(f"\n  📍 {name}")
        if loc:
            print(f"     📌 {loc} ({reg})")
        elif reg and reg != 'Other':
            print(f"     🌏 {reg}")
        if start_date:
            print(f"     📅 {start_date}")
        if cfp_deadline:
            print(f"     ✏️  CFP: {cfp_deadline}{cfp_flag}")
        if cfp_url and cfp_url != website:
            print(f"     📝 Submit: {cfp_url}")
        if website:
            print(f"     🔗 {website}")
        print(f"     🔖 {src}")

    print(f"\n{'═'*70}")
    print(f"  Total: {len(rows)}")
    print(f"{'═'*70}\n")
    conn.close()


def summary():
    conn = sqlite3.connect(DATABASE_FILE)
    c = conn.cursor()
    today_s = TODAY.isoformat()
    cutoff_s = CUTOFF.isoformat()

    print(f"\n{'='*60}")
    print(f"🌍 UPCOMING PRACTITIONER CONFERENCES (next 12 months)")
    print(f"   Window: {today_s} → {cutoff_s}")
    print(f"{'='*60}\n")

    c.execute("""SELECT region, COUNT(*) FROM conferences
                 WHERE start_date BETWEEN ? AND ?
                 GROUP BY region ORDER BY COUNT(*) DESC""", (today_s, cutoff_s))
    total = 0
    for region, cnt in c.fetchall():
        bar = '█' * min(cnt, 40)
        print(f"  {region:<12} {cnt:>3}  {bar}")
        total += cnt
    print(f"\n  {'─'*30}")
    print(f"  {'TOTAL':<12} {total:>3}")

    c.execute("SELECT COUNT(*) FROM conferences WHERE discovered_at >= datetime('now','-1 day')")
    print(f"\n  ✨ New in last 24h: {c.fetchone()[0]}")

    c.execute("""SELECT COUNT(*) FROM conferences
                 WHERE cfp_deadline BETWEEN date('now') AND date('now','+30 days')""")
    cfp_cnt = c.fetchone()[0]
    print(f"  ✏️  CFPs closing in 30 days: {cfp_cnt}")

    if cfp_cnt:
        c.execute("""SELECT name, cfp_deadline, region FROM conferences
                     WHERE cfp_deadline BETWEEN date('now') AND date('now','+30 days')
                     ORDER BY cfp_deadline LIMIT 10""")
        print(f"\n  🔥 URGENT CFPs (submit soon!):")
        for name, dl, reg in c.fetchall():
            try:
                days = (datetime.strptime(dl, '%Y-%m-%d') - datetime.now()).days
                print(f"     [{reg:>8}] {name} — {dl} ({days}d)")
            except Exception:
                print(f"     {name} — {dl}")

    # Source breakdown
    c.execute("""SELECT source, COUNT(*) FROM conferences
                 WHERE start_date BETWEEN ? AND ?
                 GROUP BY source ORDER BY COUNT(*) DESC""", (today_s, cutoff_s))
    print(f"\n  📊 By source:")
    for src, cnt in c.fetchall():
        print(f"     {src:<30} {cnt:>3}")

    print()
    conn.close()


# ════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*70)
    print("🔍 SMART PRACTITIONER CYBERSECURITY CONFERENCE SCRAPER v4")
    print(f"   Window: TODAY ({TODAY.isoformat()}) → +12 months ({CUTOFF.isoformat()})")
    print(f"   Playwright: {'✓ Available' if PLAYWRIGHT_AVAILABLE else '✗ Not installed (SPAs skipped)'}")
    print("="*70 + "\n")

    init_db()

    # Scrapers ordered by reliability and data quality
    scrapers = [
        ("xsa/infosec-events",    scrape_xsa),             # Best: community markdown table
        ("Cryptax/confsec",       scrape_cryptax),          # Great: hand-curated hacking list
        ("developers.events",     scrape_developers_events),# New: huge JSON API, security tag filter
        ("Red Canary CFP",        scrape_red_canary),       # Good: monthly CFP roundup
        ("Infosec-Conferences",   scrape_infosec_confs),    # Broad: filtered
        ("CFPTime (Playwright)",  scrape_cfptime_playwright), # SPA: optional
        ("cfp.directory (Playwright)", scrape_cfp_directory_playwright), # SPA: optional
    ]

    for name, scraper in scrapers:
        try:
            scraper()
        except Exception as e:
            print(f"  ✗ {name} crashed: {e}")
        time.sleep(RATE_LIMIT)

    summary()

    for region in ["Asia", "Europe", "Americas", "Virtual", "Other"]:
        print(f"\n{'🌏' if region=='Asia' else '🇪🇺' if region=='Europe' else '🌎' if region=='Americas' else '🌐'} {region} ".ljust(70, '═'))
        display(region)


if __name__ == "__main__":
    main()