#!/usr/bin/env python3
"""
SMART Practitioner Cybersecurity Conference Scraper — v5
=========================================================

FIXES FROM v4 BUILD LOG:
  1. developers.events crash     FIX: Tags field can be dicts OR strings.
                                      Now normalises: str(tag) if dict, else tag.lower()
  2. Red Canary → 0 found        FIX: Blog URL pattern changed. Now tries both:
                                      /blog/news-events/ AND /blog/ slugs.
                                      Also searches via DuckDuckGo as fallback.
  3. BSides312 (Chicago) → Asia  FIX: "IL" was matching Israel ISO code. Now requires
                                      ISO codes to be standalone tokens, NOT inside
                                      parentheses like "(IL)" which are US state abbrevs.
  4. Paris/Berlin → Other        FIX: classify_region now checks city dict BEFORE ISO
                                      tokens so "Berlin :de:" → Europe via city match.
  5. Nullcon duplicate            FIX: Dedup key now normalises unicode spaces + strip.
  6. Content hub pages → DB      FIX: is_practitioner rejects "| Our Content Hub" names.

NEW: GOOGLE DORK SCRAPER (Source 8)
  + Uses DuckDuckGo HTML endpoint — NO API KEY REQUIRED
  + Runs 12+ targeted security-conference dork queries
  + Deduplicates found URLs, visits each page
  + Extracts: name, date, location, CFP deadline, website
  + Rate-limited to respect robots.txt spirit
  + Falls back gracefully on network errors

  Dork query examples:
    "call for papers" "2026" cybersecurity conference
    site:bsides* 2026 CFP
    "security conference" 2026 "submit your talk"
    intitle:"CFP" "2026" infosec OR hacking conference
    ...

INSTALL:
  pip install requests beautifulsoup4 python-dateutil
  pip install playwright && playwright install chromium   (optional)

RUN:
  python3 scraper_v5.py

AUTOMATE (daily cron):
  0 9 * * * cd /path/to/scraper && python3 scraper_v5.py >> scraper.log 2>&1
"""

import sqlite3, requests, re, time, os, json, random, urllib.parse
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
MIN_YEAR   = 2025
MAX_YEAR   = 2027
RATE_LIMIT = 1.5          # seconds between HTTP requests
DDG_DELAY  = 3.0          # extra delay between DuckDuckGo calls (be polite)
TODAY      = datetime.now().date()
CUTOFF     = TODAY + timedelta(days=365)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/121.0.0.0 Safari/537.36",
    "Accept":     "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# ════════════════════════════════════════════════════════════════════════
#  REGION CLASSIFIER  (v5: city check BEFORE ISO so "Berlin :de:" → Europe)
# ════════════════════════════════════════════════════════════════════════

FLAG_EMOJI = {
    '🇮🇳': ('India', 'Asia'),       '🇸🇬': ('Singapore', 'Asia'),
    '🇯🇵': ('Japan', 'Asia'),        '🇰🇷': ('South Korea', 'Asia'),
    '🇹🇼': ('Taiwan', 'Asia'),       '🇭🇰': ('Hong Kong', 'Asia'),
    '🇮🇩': ('Indonesia', 'Asia'),    '🇹🇭': ('Thailand', 'Asia'),
    '🇲🇾': ('Malaysia', 'Asia'),     '🇵🇭': ('Philippines', 'Asia'),
    '🇻🇳': ('Vietnam', 'Asia'),      '🇦🇪': ('UAE', 'Asia'),
    '🇨🇳': ('China', 'Asia'),        '🇵🇰': ('Pakistan', 'Asia'),
    '🇱🇰': ('Sri Lanka', 'Asia'),    '🇧🇩': ('Bangladesh', 'Asia'),
    '🇳🇵': ('Nepal', 'Asia'),        '🇮🇱': ('Israel', 'Asia'),
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
    '🇺🇸': ('USA', 'Americas'),      '🇨🇦': ('Canada', 'Americas'),
    '🇲🇽': ('Mexico', 'Americas'),   '🇧🇷': ('Brazil', 'Americas'),
    '🇦🇷': ('Argentina', 'Americas'),'🇨🇱': ('Chile', 'Americas'),
    '🇨🇴': ('Colombia', 'Americas'), '🇵🇪': ('Peru', 'Americas'),
    '🇿🇦': ('South Africa', 'Other'), '🇳🇬': ('Nigeria', 'Other'),
    '🇦🇺': ('Australia', 'Other'),   '🇳🇿': ('New Zealand', 'Other'),
}

# FIX v5: ISO codes MUST be standalone (not US state abbrevs like "IL", "CA", "MD")
# We list only 2-letter country codes that should trigger region lookup.
# We deliberately EXCLUDE US state codes (IL, CA, FL, MD, VA, OH, NC, PA, WI, NE, IA)
ISO_REGION = {
    'sg':'Asia','in':'Asia','th':'Asia','jp':'Asia','kr':'Asia','tw':'Asia',
    'hk':'Asia','cn':'Asia','my':'Asia','ph':'Asia','id':'Asia','vn':'Asia',
    'ae':'Asia','pk':'Asia',
    # 'il' REMOVED — conflicts with Illinois (IL). Use flag emoji 🇮🇱 for Israel.
    'gb':'Europe','uk':'Europe','de':'Europe','fr':'Europe','nl':'Europe',
    'be':'Europe','ch':'Europe','at':'Europe','lu':'Europe','pt':'Europe',
    'es':'Europe','it':'Europe','pl':'Europe','se':'Europe','no':'Europe',
    'dk':'Europe','fi':'Europe','cz':'Europe','ro':'Europe','gr':'Europe',
    'hu':'Europe','ie':'Europe','sk':'Europe','hr':'Europe','rs':'Europe',
    'ua':'Europe','ee':'Europe','lv':'Europe','lt':'Europe','si':'Europe',
    'us':'Americas','ca':'Americas','mx':'Americas','br':'Americas',
    'ar':'Americas','co':'Americas','cl':'Americas',
}

# FIX v5: US state abbreviations that must NOT be treated as country ISO codes
US_STATE_ABBREVS = {
    'al','ak','az','ar','ca','co','ct','de','fl','ga','hi','id','il','in',
    'ia','ks','ky','la','me','md','ma','mi','mn','ms','mo','mt','ne','nv',
    'nh','nj','nm','ny','nc','nd','oh','ok','or','pa','ri','sc','sd','tn',
    'tx','ut','vt','va','wa','wv','wi','wy','dc',
}

CITY_REGION = {
    'goa':'Asia','kochi':'Asia','pune':'Asia','hyderabad':'Asia',
    'kolkata':'Asia','manila':'Asia','cebu':'Asia','ho chi minh':'Asia',
    'hanoi':'Asia','jakarta':'Asia','kuala lumpur':'Asia','penang':'Asia',
    'osaka':'Asia','kyoto':'Asia','nagoya':'Asia','fukuoka':'Asia',
    'abu dhabi':'Asia','riyadh':'Asia','doha':'Asia','muscat':'Asia',
    'tel aviv':'Asia','jerusalem':'Asia','tokyo':'Asia','seoul':'Asia',
    'bangkok':'Asia','taipei':'Asia','hong kong':'Asia','beijing':'Asia',
    'shanghai':'Asia','dubai':'Asia',
    'hamburg':'Europe','munich':'Europe','frankfurt':'Europe','cologne':'Europe',
    'düsseldorf':'Europe','dusseldorf':'Europe','hannover':'Europe',
    'heidelberg':'Europe','dresden':'Europe','stuttgart':'Europe',
    'berlin':'Europe','dortmund':'Europe','leipzig':'Europe',
    'rennes':'Europe','lyon':'Europe','toulouse':'Europe','bordeaux':'Europe',
    'marseille':'Europe','lille':'Europe','strasbourg':'Europe','brest':'Europe',
    'reims':'Europe','nantes':'Europe','nice':'Europe','sophia antipolis':'Europe',
    'paris':'Europe','clermont ferrand':'Europe','clermont-ferrand':'Europe',
    'grenoble':'Europe',
    'barcelona':'Europe','madrid':'Europe','seville':'Europe','bilbao':'Europe',
    'malaga':'Europe','málaga':'Europe','jaén':'Europe',
    'milan':'Europe','florence':'Europe','naples':'Europe','catania':'Europe',
    'venice':'Europe','torino':'Europe','turin':'Europe','bertinoro':'Europe',
    'rome':'Europe','bologna':'Europe',
    'edinburgh':'Europe','glasgow':'Europe','manchester':'Europe',
    'birmingham':'Europe','bristol':'Europe','cambridge':'Europe',
    'oxford':'Europe','leeds':'Europe','belfast':'Europe','cardiff':'Europe',
    'london':'Europe',
    'dublin':'Europe','cork':'Europe',
    'zurich':'Europe','zürich':'Europe','geneva':'Europe','basel':'Europe',
    'bern':'Europe','lausanne':'Europe','belval':'Europe',
    'prague':'Europe','brno':'Europe','bratislava':'Europe',
    'budapest':'Europe','krakow':'Europe','warsaw':'Europe','wroclaw':'Europe',
    'stockholm':'Europe','gothenburg':'Europe','gothenberg':'Europe',
    'malmo':'Europe','umea':'Europe','linkoping':'Europe',
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
    'mesa':'Americas','sunny isles':'Americas','durham':'Americas',
    'harrisburg':'Americas','fairborn':'Americas','dayton':'Americas',
    'san jose':'Americas','los angeles':'Americas','portland':'Americas',
    'minneapolis':'Americas','raleigh':'Americas','charlotte':'Americas',
}

def classify_region(text: str) -> str:
    if not text:
        return 'Other'
    # 1. Flag emoji (most reliable)
    for emoji, (_, region) in FLAG_EMOJI.items():
        if emoji in text:
            return region
    t = text.lower().strip()

    # 2. City names FIRST (before ISO to avoid misclassification)
    for city, region in CITY_REGION.items():
        if city in t:
            return region

    # 3. ISO-2 codes as standalone tokens — but skip US state abbrevs
    # Only match if it's surrounded by spaces/punctuation, NOT inside parens like "(IL)"
    # This prevents "Chicago (IL)" from matching ISO 'il'
    tokens = re.split(r'[\s,;:()\[\]]+', t)
    for part in tokens:
        part = part.strip().rstrip('.')
        if len(part) == 2 and part in ISO_REGION and part not in US_STATE_ABBREVS:
            return ISO_REGION[part]

    # 4. Country/continent keywords
    if any(k in t for k in ['india','singapore','thailand','malaysia','china',
                              'japan','korea','taiwan','indonesia','philippines',
                              'vietnam','uae','dubai','hong kong','israel']):
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
    if not s or any(x in str(s).lower() for x in ['tba','tbd','n/a','coming soon','none','-\n','invite']):
        return None
    s = str(s).strip()
    if re.fullmatch(r'202[4-7]', s):
        return None
    if re.match(r'(spring|summer|fall|autumn|winter|q[1-4])\s*202[4-7]', s.lower()):
        return None
    if re.fullmatch(r'(january|february|march|april|may|june|july|august|'
                    r'september|october|november|december)\s+202[4-7]', s.strip(), re.I):
        return None
    try:
        cleaned = re.sub(r'\([^)]*\)', '', s).replace('\u2013','-').replace('\u2014','-').strip()
        cross_m = re.match(r'(\w+\s+\d{1,2})\s*[-–]\s*\w+\s+\d{1,2}(,?\s*20\d{2}.*)', cleaned)
        if cross_m:
            cleaned = cross_m.group(1) + cross_m.group(2)
        else:
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
            if d > CUTOFF:
                return None
            return d.isoformat()
    except Exception:
        pass
    return None


def from_ms_timestamp(ts) -> Optional[str]:
    try:
        d = datetime.utcfromtimestamp(int(ts) / 1000).date()
        if MIN_YEAR <= d.year <= MAX_YEAR and d >= TODAY and d <= CUTOFF:
            return d.isoformat()
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════════════════
#  PRACTITIONER FILTER
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
    "auvergn","m0lecon","grehack","cackalacky","typhooncon",
    "first vulncon","first conference","first cti","virusbulletin",
    "virus bulletin","offcon","secfest","cactuscon","owasp","labscon",
    "lehack","ph0wn","ootb","out of the box","seccon","pivotcon",
    "nsec","northsec","breizhctf","orangecon","reverse","re//verse",
}

WEBINAR_BLOCKLIST = [
    r'^\d{1,2}(st|nd|rd|th)\s+\w+\s+\d{4}\s*\|',
    r'^(how|why|what|when|where|top\s+\d|understanding|introduction|insights)',
    r'\bwebinar\b', r'\bbootcamp\b', r'\bvirtual summit\b', r'\bonline summit\b',
    r'\bvirtual event\b', r'\bhow to\b', r'\btips for\b', r'\bbriefing\b',
    r'\bperspectives from\b', r'\binsights on\b', r'\btactical\b',
    r'\bpredictions for\b', r'\blearn how\b', r'\bstrategies for\b',
    r'\bfive trends\b', r'\bbenefits of\b',
    r'our content hub',    # FIX v5: blocks "GRC | Our Content Hub" etc.
    r'content hub',
]

ACADEMIC_BLOCKLIST_TERMS = [
    "workshop on","symposium on","proceedings of","acm sigsac",
    "ieee s&p","ieee symposium","acm ccs","usenix security","ndss","eurocrypt",
    "asiacrypt","workshop","symposium","seminar","colloquium",
    "ieee ","acm ","iacr ","springer ","elsevier ",
    "peer review","camera ready","double blind","acceptance rate",
    "cpss","fl asiaccs","apkc","hasp","artman","ccsw","wpes","optimist",
    "weis","satml","eurosec","codaspy","acsac",
]

def is_practitioner(name: str, desc: str = "", source: str = "") -> bool:
    name_l = (name or "").lower().strip()
    combined = name_l + " " + (desc or "").lower()
    if any(a in name_l for a in PRACTITIONER_ALLOWLIST):
        return True
    if source in ("xsa/infosec-events","Cryptax/confsec","CFPTime","Red Canary CFP"):
        return True
    for pattern in WEBINAR_BLOCKLIST:
        if re.search(pattern, name_l, re.I):
            return False
    if any(b in combined for b in ACADEMIC_BLOCKLIST_TERMS):
        return False
    return True


def clean_name(raw: str) -> str:
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
    c.execute("PRAGMA table_info(conferences)")
    cols = {r[1] for r in c.fetchall()}
    for col in ("cfp_url","end_date"):
        if col not in cols:
            c.execute(f"ALTER TABLE conferences ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()
    print("✓ Database ready")


def _save(conf: Dict, source: str) -> bool:
    name = (conf.get('name') or '').strip()
    # Normalise unicode spaces / zero-width chars
    name = re.sub(r'[\u200b\u00a0\ufeff]', '', name).strip()
    if not name or len(name) < 3:
        return False
    if not is_practitioner(name, conf.get('description',''), source):
        return False
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
        c.execute("SELECT id FROM conferences WHERE name=? AND start_date=?", (name, sd))
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
#  SOURCE 1: xsa/infosec-events
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
            raise Exception(f"HTTP {r.status_code}")
        text = r.text
        in_table = False
        for line in text.split('\n'):
            if '|' not in line:
                in_table = False
                continue
            cells = [c.strip() for c in line.split('|')]
            cells = [c for c in cells if c]
            if not cells:
                continue
            if re.search(r'(event|name|conference)', cells[0], re.I) and len(cells) >= 3:
                in_table = True
                continue
            if all(re.match(r'^[-:]+$', c.replace(' ', '')) for c in cells):
                continue
            if not in_table:
                continue
            if len(cells) < 2:
                continue

            name = cells[0].strip()
            link_m = re.search(r'\[([^\]]+)\]\(([^)]+)\)', name)
            website = None
            if link_m:
                name = link_m.group(1)
                website = link_m.group(2)
            name = re.sub(r'[\*`\[\]]', '', name).strip()
            if not name or len(name) < 2:
                continue

            date_str = cells[1] if len(cells) > 1 else ''
            location_raw = cells[2] if len(cells) > 2 else ''

            flag_found = None
            for emoji in FLAG_EMOJI:
                if emoji in location_raw:
                    flag_found = emoji
                    break

            location = re.sub(r'[\U0001F1E0-\U0001F1FF]{2}', '', location_raw).strip()

            if flag_found:
                country, region = FLAG_EMOJI[flag_found]
            else:
                country = None
                region = classify_region(location_raw)

            year_match = re.search(r'(202[4-7])', date_str)
            year_hint = int(year_match.group(1)) if year_match else (
                TODAY.year if TODAY.month <= 6 else TODAY.year + 1)
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
#  SOURCE 2: Cryptax/confsec
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
            cells = [c for c in cells if c]

            if re.search(r'(date|conf|event|name)', cells[0] if cells else '', re.I):
                in_table = True
                for i, h in enumerate(cells):
                    hl = h.lower()
                    if re.search(r'date|when', hl):          date_col = i
                    elif re.search(r'conf|name|event', hl):  name_col = i
                    elif re.search(r'loc|where|city', hl):   loc_col  = i
                    elif re.search(r'cfp|deadline', hl):     cfp_col  = i
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

            website = None
            name = name_cell
            m = re.search(r'\[([^\]]+)\]\(([^)]+)\)', name_cell)
            if m:
                name, website = m.group(1), m.group(2)
            name = re.sub(r'[\*\[\]`]', '', name).strip()
            if not name or len(name) < 3:
                continue
            if re.match(r'^[A-Z][a-z]+,\s+[A-Z]', name) and len(name.split()) <= 3:
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
#  SOURCE 3: developers.events/all-events.json  — FIXED
#  FIX v5: Tags can be dicts ({"name":"security","slug":"security"}) OR strings.
#          Normalise both: extract .get('name') from dict, use str directly.
# ════════════════════════════════════════════════════════════════════════

SECURITY_KEYWORDS = {
    'security','cybersecurity','infosec','hacking','pentest','penetration',
    'ctf','vulnerability','malware','forensics','devsecops','appsec',
    'owasp','threat','exploit','reverse','cryptography','privacy','cyber',
    'bsides','defcon','blackhat','nullcon','hitb','sstic','botconf',
}

def _normalise_tag(tag) -> str:
    """Accept string or dict tag from developers.events JSON."""
    if isinstance(tag, str):
        return tag.lower()
    if isinstance(tag, dict):
        return str(tag.get('name', tag.get('label', tag.get('tag', '')))).lower()
    return str(tag).lower()

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
            name = ev.get('name','').strip()
            # FIX: normalise tags whether they're strings or dicts
            raw_tags = ev.get('tags') or []
            tags = [_normalise_tag(t) for t in raw_tags]

            city    = ev.get('city','')
            country = ev.get('country','')
            website = ev.get('hyperlink','')
            location = ev.get('location', f"{city}, {country}".strip(', '))

            name_l = name.lower()
            has_sec_tag  = any(kw in t for t in tags for kw in SECURITY_KEYWORDS)
            has_sec_name = any(kw in name_l for kw in SECURITY_KEYWORDS)
            if not has_sec_tag and not has_sec_name:
                continue

            raw_dates = ev.get('date', [])
            start_date = end_date = None
            if raw_dates:
                start_date = from_ms_timestamp(raw_dates[0])
                if len(raw_dates) > 1:
                    end_date = from_ms_timestamp(raw_dates[-1])

            cfp_obj      = ev.get('cfp') or {}
            cfp_url      = cfp_obj.get('link','') if cfp_obj else None
            cfp_deadline = None
            if cfp_obj.get('until'):
                cfp_deadline = from_ms_timestamp(cfp_obj['until'])

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
#  FIX v5: Red Canary moved posts to /blog/ (no "news-events" subpath).
#           Now tries both URL patterns, plus a DuckDuckGo lookup fallback.
# ════════════════════════════════════════════════════════════════════════

def scrape_red_canary() -> List[Dict]:
    source = "Red Canary CFP"
    results = []
    print(f"  → Fetching {source}...")
    try:
        now = datetime.now()
        url_candidates = []
        for delta in range(8):
            m = now.month - delta
            y = now.year
            while m <= 0:
                m += 12
                y -= 1
            month_name = datetime(y, m, 1).strftime('%B').lower()
            # Both URL patterns (old and new)
            url_candidates.append(
                f"https://redcanary.com/blog/news-events/cfp-tracker-{month_name}-{y}/")
            url_candidates.append(
                f"https://redcanary.com/blog/cfp-tracker-{month_name}-{y}/")

        soup = None
        found_url = None
        for url in url_candidates:
            try:
                r = requests.get(url, headers=HEADERS, timeout=15, verify=False)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.content, 'html.parser')
                    found_url = url
                    print(f"    ✓ Found: {url.split('redcanary.com')[-1]}")
                    break
            except Exception:
                continue

        # Fallback: DuckDuckGo search for latest CFP tracker post
        if not soup:
            print("    ↳ Trying DuckDuckGo fallback for Red Canary CFP...")
            ddg_results = _ddg_search("site:redcanary.com cfp tracker 2026", max_results=5)
            for ddg_url in ddg_results:
                if 'cfp-tracker' in ddg_url and 'redcanary.com' in ddg_url:
                    try:
                        r = requests.get(ddg_url, headers=HEADERS, timeout=15, verify=False)
                        if r.status_code == 200:
                            soup = BeautifulSoup(r.content, 'html.parser')
                            found_url = ddg_url
                            print(f"    ✓ Found via DDG: {ddg_url}")
                            break
                    except Exception:
                        continue

        if not soup:
            raise Exception("No CFP tracker post found — Red Canary may have changed URL structure")

        tables = soup.find_all('table')
        if not tables:
            content = soup.find('main') or soup.find('article') or soup.find('body')
            if content:
                for line in content.get_text(separator='\n').split('\n'):
                    if line.count('|') >= 2:
                        parts = [p.strip() for p in line.split('|')]
                        name = parts[0].strip()
                        if not name or len(name) < 3 or name.lower().startswith(
                                ('conf','name','event','---')):
                            continue
                        location  = parts[1].strip() if len(parts) > 1 else None
                        conf_date = parts[2].strip() if len(parts) > 2 else None
                        cfp_date  = parts[3].strip() if len(parts) > 3 else None
                        cfp_url_s = parts[4].strip() if len(parts) > 4 else None
                        if name and (parse_date(conf_date) or parse_date(cfp_date)):
                            results.append({
                                'name': name, 'location': location, 'country': None,
                                'region': classify_region(location or ''),
                                'start_date': parse_date(conf_date), 'end_date': None,
                                'cfp_deadline': parse_date(cfp_date),
                                'cfp_url': cfp_url_s or found_url,
                                'website': found_url, 'description': 'CFP open',
                            })
        else:
            for table in tables:
                headers = []
                header_row = table.find('tr')
                if header_row:
                    headers = [th.get_text(strip=True).lower()
                               for th in header_row.find_all(['th','td'])]

                name_i = loc_i = date_i = cfp_i = cfpurl_i = None
                for i, h in enumerate(headers):
                    if re.search(r'name|conference|event', h): name_i = i
                    elif re.search(r'loc|city|where', h):      loc_i  = i
                    elif re.search(r'date|when', h) and cfp_i is None: date_i = i
                    elif re.search(r'cfp.*date|deadline', h):  cfp_i  = i
                    elif re.search(r'cfp.*link|submit|url', h): cfpurl_i = i
                if name_i is None: name_i = 0
                if date_i is None: date_i = 2
                if cfp_i  is None: cfp_i  = 3

                for row in table.find_all('tr')[1:]:
                    cells = row.find_all(['td','th'])
                    if not cells:
                        continue
                    def cell(i): return cells[i].get_text(strip=True) if i < len(cells) else ''
                    name = cell(name_i)
                    if not name or len(name) < 3:
                        continue
                    location    = cell(loc_i) if loc_i is not None else None
                    conf_date   = cell(date_i)
                    cfp_date    = cell(cfp_i)
                    cfp_url_str = cell(cfpurl_i) if cfpurl_i is not None else None
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
#  SOURCE 5: Infosec-Conferences.com — with content hub filter
# ════════════════════════════════════════════════════════════════════════

def scrape_infosec_confs() -> List[Dict]:
    source = "Infosec-Conferences.com"
    results = []
    print(f"  → Fetching {source}...")
    try:
        soup = _fetch("https://infosec-conferences.com/")
        if not soup:
            raise Exception("Failed to fetch main page")

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
#  SOURCE 6 & 7: CFPTime + cfp.directory (Playwright, optional)
# ════════════════════════════════════════════════════════════════════════

def scrape_cfptime_playwright() -> List[Dict]:
    source = "CFPTime"
    results = []
    if not PLAYWRIGHT_AVAILABLE:
        print(f"  ⚠ {source}: Playwright not installed (skipping).")
        return []
    print(f"  → Fetching {source} (Playwright)...")
    captured_json = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"])
            page = ctx.new_page()
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
                page.wait_for_selector("table, .conference-row, [class*=conference]", timeout=10000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
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
                            'website': 'https://cfptime.org/upcoming', 'description': None,
                        })
            browser.close()
    except Exception as e:
        _log(source, 0, 0, 0, 'error', str(e))
        print(f"  ✗ {source}: {e}")
        return []

    for data in captured_json:
        items = data if isinstance(data, list) else data.get('results', data.get('conferences', []))
        for item in (items if isinstance(items, list) else []):
            name = item.get('name', item.get('conference',''))
            if not name: continue
            location = item.get('location', item.get('city',''))
            country  = item.get('country','')
            start_date   = parse_date(item.get('start_date', item.get('date','')))
            cfp_deadline = parse_date(item.get('cfp_deadline', item.get('deadline','')))
            website  = item.get('url', item.get('website',''))
            if len(name) > 3:
                results.append({
                    'name': name, 'location': location, 'country': country,
                    'region': classify_region(f"{location} {country}"),
                    'start_date': start_date, 'end_date': None,
                    'cfp_deadline': cfp_deadline, 'cfp_url': website,
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


def scrape_cfp_directory_playwright() -> List[Dict]:
    source = "cfp.directory"
    results = []
    if not PLAYWRIGHT_AVAILABLE:
        print(f"  ⚠ {source}: Playwright not installed (skipping).")
        return []
    print(f"  → Fetching {source} (Playwright)...")
    captured_json = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(user_agent=HEADERS["User-Agent"])
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
                page.wait_for_selector("[class*=event], [class*=conference], table, li", timeout=10000)
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
#  SOURCE 8: GOOGLE DORK SCRAPER (NEW — uses DuckDuckGo, no API key needed)
# ════════════════════════════════════════════════════════════════════════
#
#  Strategy:
#   1. Run a set of targeted dork queries against DuckDuckGo HTML endpoint
#   2. Collect all unique URLs from results (dedup by domain+path)
#   3. Visit each URL, extract conference details (name, date, location, CFP)
#   4. Intelligent extraction: looks for structured data (JSON-LD), then
#      Open Graph tags, then heuristic HTML patterns
#   5. Rate-limit aggressively to be a good citizen
#
#  Dork queries designed to find:
#   - Official conference CFP pages
#   - BSides events not yet in community lists
#   - Niche practitioner conferences
#   - Conference aggregator pages
# ════════════════════════════════════════════════════════════════════════

# Dork queries — each targets a different angle of conference discovery
DORK_QUERIES = [
    # CFP-focused
    '"call for papers" "2026" cybersecurity conference hacking',
    '"call for speakers" "2026" security conference infosec',
    '"submit your talk" "2026" security hacking conference',
    '"CFP open" OR "CFP closes" "2026" hacking security conference',
    'intitle:"call for papers" "2026" "security" site:*.io OR site:*.org',
    # BSides discovery
    'site:bsides* "2026" CFP',
    '"BSides" "2026" conference "call for papers" -site:twitter.com',
    # Niche/regional
    '"infosec" "2026" conference "registration open" -site:eventbrite.com',
    '"hacking" "cybersecurity" conference "2026" "tickets" -site:meetup.com',
    '"security conference" "2026" "speakers" -site:linkedin.com',
    # Aggregator/list pages
    'cybersecurity conferences 2026 schedule list upcoming',
    '"hacker conference" 2026 schedule dates',
    # Location-specific
    '"security conference" "2026" Europe schedule dates',
    '"infosec" conference 2026 Asia Singapore',
]

# Domains to skip when visiting found URLs (too generic / not conference-specific)
SKIP_DOMAINS = {
    'twitter.com','x.com','linkedin.com','facebook.com','instagram.com',
    'youtube.com','reddit.com','github.com','wikipedia.org','medium.com',
    'eventbrite.com','meetup.com','amazon.com','google.com','bing.com',
    'duckduckgo.com','glassdoor.com','indeed.com','coursera.org','udemy.com',
    'sans.org',  # good but separate, add explicitly if you want it
}

# Domains that are strong signals for practitioner conferences
HIGH_SIGNAL_DOMAINS = {
    'bsides','defcon','blackhat','nullcon','troopers','hitb','sstic',
    'botconf','brucon','sec-t','elbsides','recon','shmoocon','ekoparty',
    'kernelcon','northsec','nsec','hackmiami','corncon','thotcon','cactuscon',
    'insomnihack','area41','offensivecon','hardwear','pass-the-salt',
    'securityfest','grehack','hexacon','hack.lu','ph0wn',
}


def _ddg_search(query: str, max_results: int = 10) -> List[str]:
    """
    Search DuckDuckGo HTML endpoint.
    Returns list of result URLs (no API key needed).
    DDG rate-limits aggressively; always call with DDG_DELAY between queries.
    """
    urls = []
    try:
        encoded = urllib.parse.quote_plus(query)
        search_url = f"https://html.duckduckgo.com/html/?q={encoded}"
        r = requests.get(search_url, headers={
            **HEADERS,
            "Referer": "https://duckduckgo.com/",
        }, timeout=20)
        if r.status_code != 200:
            return urls
        soup = BeautifulSoup(r.content, 'html.parser')
        # DDG HTML results are in <a class="result__a"> tags
        for a in soup.select('a.result__a'):
            href = a.get('href', '')
            # DDG wraps URLs in //duckduckgo.com/l/?uddg=<encoded_url>
            if 'uddg=' in href:
                try:
                    href = urllib.parse.unquote(
                        href.split('uddg=')[1].split('&')[0])
                except Exception:
                    continue
            if href.startswith('http') and href not in urls:
                urls.append(href)
            if len(urls) >= max_results:
                break
        # Also try plain <a> tags in result blocks
        if len(urls) < 3:
            for a in soup.select('.result__url'):
                text = a.get_text(strip=True)
                if text.startswith('http'):
                    if text not in urls:
                        urls.append(text)
    except Exception as e:
        print(f"      DDG search error: {e}")
    return urls


def _extract_conf_details(url: str, soup: BeautifulSoup) -> Optional[Dict]:
    """
    Extract conference details from a visited page.
    Priority order:
      1. JSON-LD structured data (schema.org Event)
      2. Open Graph meta tags
      3. Heuristic: title + date/location patterns in body text
    """
    page_text = soup.get_text(separator=' ', strip=True)
    page_lower = page_text.lower()

    # Must look like a security conference page
    security_signals = sum(1 for kw in [
        'conference','con','cfp','hacking','security','infosec','call for',
        'speakers','talk','workshop','registration','tickets'
    ] if kw in page_lower)
    if security_signals < 2:
        return None

    # Must mention a future year
    if not re.search(r'202[6-7]', page_text):
        return None

    result = {
        'name': None, 'location': None, 'country': None, 'region': 'Other',
        'start_date': None, 'end_date': None, 'cfp_deadline': None,
        'cfp_url': None, 'website': url, 'description': None,
    }

    # --- 1. JSON-LD schema.org/Event ---
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string or '')
            if isinstance(data, list):
                data = data[0]
            if not isinstance(data, dict):
                continue
            etype = data.get('@type', '')
            if 'Event' not in str(etype) and 'Conference' not in str(etype):
                continue
            result['name'] = data.get('name', result['name'])
            raw_start = data.get('startDate', '')
            if raw_start:
                result['start_date'] = parse_date(str(raw_start)[:10])
            raw_end = data.get('endDate', '')
            if raw_end:
                result['end_date'] = parse_date(str(raw_end)[:10])
            loc = data.get('location', {})
            if isinstance(loc, dict):
                addr = loc.get('address', {})
                if isinstance(addr, dict):
                    city    = addr.get('addressLocality', '')
                    country = addr.get('addressCountry', '')
                    result['location'] = f"{city}, {country}".strip(', ')
                elif isinstance(addr, str):
                    result['location'] = addr
                result['location'] = result['location'] or loc.get('name', '')
            elif isinstance(loc, str):
                result['location'] = loc
            if result['name'] and result['start_date']:
                result['region'] = classify_region(result['location'] or '')
                return result
        except Exception:
            continue

    # --- 2. Open Graph meta tags ---
    og_title = soup.find('meta', property='og:title')
    if og_title and og_title.get('content'):
        result['name'] = og_title['content'].strip()

    og_desc = soup.find('meta', property='og:description')
    if og_desc and og_desc.get('content'):
        result['description'] = og_desc['content'][:300]

    # --- 3. Heuristic extraction ---
    # Name: try <h1> first, then <title>
    if not result['name']:
        h1 = soup.find('h1')
        if h1:
            result['name'] = h1.get_text(strip=True)[:120]
    if not result['name']:
        title = soup.find('title')
        if title:
            result['name'] = title.get_text(strip=True).split('|')[0].split('–')[0].strip()[:120]

    # Validate name looks like a conference, not a generic page title
    if result['name']:
        name_l = result['name'].lower()
        if any(bad in name_l for bad in ['home','index','welcome','page not found',
                                          '404','error','login','sign in']):
            return None
        # Must have some conference-y word
        if not any(kw in name_l for kw in [
            'con','sec','hack','security','conference','summit','fest',
            'camp','ctf','forum','symposium'
        ]):
            # Allow if URL domain has signal
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower()
            if not any(sig in domain for sig in HIGH_SIGNAL_DOMAINS):
                return None

    # Date: scan body text for date patterns
    date_patterns = [
        r'\b(january|february|march|april|may|june|july|august|september|'
        r'october|november|december)\s+\d{1,2}(?:[-–]\d{1,2})?,?\s+202[6-7]\b',
        r'\b\d{1,2}\s+(january|february|march|april|may|june|july|august|'
        r'september|october|november|december)\s+202[6-7]\b',
        r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+'
        r'\d{1,2}(?:[-–]\d{1,2})?,?\s+202[6-7]\b',
        r'\b202[6-7][-/]\d{2}[-/]\d{2}\b',   # ISO format
    ]
    if not result['start_date']:
        for pat in date_patterns:
            m = re.search(pat, page_text, re.I)
            if m:
                result['start_date'] = parse_date(m.group(0))
                if result['start_date']:
                    break

    # Location: look for "Location:", "Venue:", city patterns
    if not result['location']:
        loc_patterns = [
            r'(?:location|venue|where|city)\s*:?\s*([A-Z][a-zA-Z\s,]+)',
            r'\bin\s+([A-Z][a-z]+(?:,\s+[A-Z][a-z]+)*)',
        ]
        for pat in loc_patterns:
            m = re.search(pat, page_text)
            if m:
                candidate = m.group(1).strip()
                if len(candidate) < 60 and classify_region(candidate) != 'Other':
                    result['location'] = candidate
                    break

    # CFP deadline: scan for "CFP closes", "deadline", "submit by"
    cfp_patterns = [
        r'(?:cfp|call for (?:papers|speakers?|talks?)).*?(?:closes?|deadline|due|ends?).*?'
        r'((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+202[6-7])',
        r'(?:deadline|submit by|submissions? (?:due|close)).*?'
        r'((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+202[6-7])',
    ]
    if not result['cfp_deadline']:
        for pat in cfp_patterns:
            m = re.search(pat, page_lower, re.I)
            if m:
                result['cfp_deadline'] = parse_date(m.group(1))
                if result['cfp_deadline']:
                    # Try to find a CFP submission link
                    for a in soup.find_all('a', href=True):
                        href = a['href']
                        atext = a.get_text(strip=True).lower()
                        if any(kw in atext for kw in ['submit','cfp','proposal','speak']):
                            if href.startswith('http'):
                                result['cfp_url'] = href
                            break
                    break

    result['region'] = classify_region(
        f"{result['location'] or ''} {result['country'] or ''}")

    # Final validation: must have a name at minimum
    if not result['name'] or len(result['name']) < 4:
        return None

    return result


def _get_domain(url: str) -> str:
    """Extract domain from URL for dedup purposes."""
    try:
        parsed = urllib.parse.urlparse(url)
        return parsed.netloc.lower().lstrip('www.')
    except Exception:
        return url


def scrape_google_dorks() -> List[Dict]:
    """
    NEW SOURCE: Google/DuckDuckGo dork-based conference discovery.
    
    Algorithm:
    1. Run DORK_QUERIES against DuckDuckGo HTML search
    2. Collect unique URLs, skip social/generic domains
    3. Visit each URL and extract conference details
    4. Prioritise high-signal domains (known conference orgs)
    """
    source = "Google Dorks (DuckDuckGo)"
    results = []
    visited_urls: set  = set()
    visited_domains: set = set()
    found_urls: List[str] = []

    print(f"  → Running dork searches ({len(DORK_QUERIES)} queries)...")

    # Phase 1: Collect URLs from all dork queries
    for i, query in enumerate(DORK_QUERIES):
        print(f"    [{i+1}/{len(DORK_QUERIES)}] {query[:70]}...")
        urls = _ddg_search(query, max_results=8)
        for url in urls:
            domain = _get_domain(url)
            # Skip blocked domains
            if any(skip in domain for skip in SKIP_DOMAINS):
                continue
            # Skip already found URLs
            if url in visited_urls:
                continue
            visited_urls.add(url)
            found_urls.append(url)
        time.sleep(DDG_DELAY)

    # Prioritise URLs from high-signal domains
    def url_priority(url):
        domain = _get_domain(url)
        if any(sig in domain for sig in HIGH_SIGNAL_DOMAINS):
            return 0   # highest priority
        return 1

    found_urls.sort(key=url_priority)
    print(f"    Found {len(found_urls)} unique URLs to visit")

    # Phase 2: Visit each URL and extract details
    visited_count = 0
    for url in found_urls:
        domain = _get_domain(url)

        # Skip if we've already scraped this domain (avoid harvesting entire sites)
        if domain in visited_domains:
            continue
        visited_domains.add(domain)

        try:
            r = requests.get(url, headers=HEADERS, timeout=15, verify=False,
                             allow_redirects=True)
            if r.status_code != 200:
                continue
            soup = BeautifulSoup(r.content, 'html.parser')
            conf = _extract_conf_details(url, soup)
            if conf:
                conf['website'] = url
                conf['source']  = source   # for internal tracking
                results.append(conf)
                print(f"      ✓ {conf['name'][:60]} | {conf.get('start_date','?')} | {conf.get('region','?')}")
        except Exception as e:
            pass   # silently skip broken pages

        visited_count += 1
        time.sleep(RATE_LIMIT)

        # Safety cap — don't hammer the internet
        if visited_count >= 60:
            print(f"    Reached 60-page cap. {len(results)} conferences extracted.")
            break

    # Dedup by name
    seen, unique = set(), []
    for r in results:
        k = (r.get('name','') or '').lower().strip()
        if k and k not in seen:
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
    today_s  = TODAY.isoformat()
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

    c.execute("""SELECT source, COUNT(*) FROM conferences
                 WHERE start_date BETWEEN ? AND ?
                 GROUP BY source ORDER BY COUNT(*) DESC""", (today_s, cutoff_s))
    print(f"\n  📊 By source:")
    for src, cnt in c.fetchall():
        print(f"     {src:<35} {cnt:>3}")

    print()
    conn.close()


# ════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "="*70)
    print("🔍 SMART PRACTITIONER CYBERSECURITY CONFERENCE SCRAPER v5")
    print(f"   Window: TODAY ({TODAY.isoformat()}) → +12 months ({CUTOFF.isoformat()})")
    print(f"   Playwright: {'✓ Available' if PLAYWRIGHT_AVAILABLE else '✗ Not installed (SPAs skipped)'}")
    print("="*70 + "\n")

    init_db()

    scrapers = [
        ("xsa/infosec-events",              scrape_xsa),
        ("Cryptax/confsec",                 scrape_cryptax),
        ("developers.events",               scrape_developers_events),   # FIXED
        ("Red Canary CFP",                  scrape_red_canary),          # FIXED
        ("Infosec-Conferences",             scrape_infosec_confs),
        ("CFPTime (Playwright)",            scrape_cfptime_playwright),
        ("cfp.directory (Playwright)",      scrape_cfp_directory_playwright),
        ("Google Dorks (DuckDuckGo)",       scrape_google_dorks),        # NEW
    ]

    for name, scraper in scrapers:
        try:
            scraper()
        except Exception as e:
            print(f"  ✗ {name} crashed: {e}")
        time.sleep(RATE_LIMIT)

    summary()

    for region in ["Asia", "Europe", "Americas", "Virtual", "Other"]:
        emoji = {'Asia':'🌏','Europe':'🇪🇺','Americas':'🌎'}.get(region,'🌐')
        print(f"\n{emoji} {region} ".ljust(70, '═'))
        display(region)


if __name__ == "__main__":
    main()