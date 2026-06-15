"""
Sarkari Portal Scraper
Scrapes sarkariresult.com.cm → Supabase `opportunities` + `opportunity_links`
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from supabase import create_client, Client

# ── Config ────────────────────────────────────────────────────

BASE_URL = "https://sarkariresult.com.cm"

# Map listing-page URL segment → (DB category, fallback)
LISTING_SECTIONS = {
    "latest-jobs": None,      # auto-detect from slug/title
    "result":      "result",
    "admit-card":  "admit_card",
    "answer-key":  "answer_key",
    "admission":   "admission",
    "syllabus":    "syllabus",
}

CATEGORY_URLS = {section: f"{BASE_URL}/{section}/" for section in LISTING_SECTIONS}

# Actual DB enum values for opportunity_category
# (discovered from PostgREST OpenAPI)
_ORG_CATEGORY_RULES = [
    (r"rrb|rrc|railway|irctc|irms", "railway"),
    (r"\bssc\b|staff.selection", "ssc"),
    (r"upsc|ias\b|ips\b|ifs\b|capf", "upsc"),
    (r"bank|ibps|sbi\b|rbi\b|nabard|sidbi|rrb.?b", "banking"),
    (r"police|bsf\b|crpf\b|cisf\b|itbp\b|ssb\b|rpf\b|cbi\b", "police"),
    (r"army|navy|airforce|nda\b|cds\b|drdo|isro|barc|bel\b|bhel|hal\b|defence", "defence"),
    (r"teacher|teaching|tgt\b|pgt\b|tet\b|ctet\b|reet|school|kvs\b|nvs\b", "teaching"),
    (r"aiims|pgimer|esic|health|medical|nurse|doctor|nhs|jipmer", "psu"),
    (r"psu|ongc|ioc|hpcl|bpcl|ntpc|nhpc|sail\b|coal.india|mecl", "psu"),
    (r"nta\b|neet|jee|cuet|ugc.net|gate\b|clat\b|entrance", "admission"),
    (r"scholar", "scholarship"),
    (r"uppsc|bpsc|rpsc|mpsc|kpsc|tspsc|wbpsc|gpsc|mppsc|opsc|ukpsc|hpsc", "state_government"),
    (r"state|district|municipal|panchayat|zila|tehsil", "state_government"),
]

_SKIP_PATHS = {
    "", "/", "latest-jobs", "latest-posts", "result", "admit-card",
    "answer-key", "admission", "syllabus", "contact", "privacy-policy",
    "disclaimer", "sitemap", "about", "about-us", "advertise",
}

_SKIP_LINK_LABELS = {
    "join our whatsapp channel", "join our telegram channel",
    "follow now", "download sarkariresult app now",
    "whatsapp", "telegram", "click here for more",
}

_SKIP_LINK_DOMAINS = {
    "googleads", "doubleclick", "googlesyndication",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "t.me", "telegram.me", "whatsapp.com",
    "play.google.com", "apps.apple.com",
}

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "1.5"))

# ── Logging ───────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=__import__("sys").stdout,
)
log = logging.getLogger("scraper")

# ── Supabase ──────────────────────────────────────────────────

def get_supabase() -> Optional[Client]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        log.warning("SUPABASE_URL/SUPABASE_SERVICE_KEY not set — local JSON only")
        return None
    return create_client(url, key)

# ── HTTP ──────────────────────────────────────────────────────

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

def fetch(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(1, retries + 1):
        try:
            r = _session.get(url, timeout=20)
            r.raise_for_status()
            return BeautifulSoup(r.text, "lxml")
        except Exception as e:
            log.warning("Attempt %d/%d %s — %s", attempt, retries, url, e)
            if attempt < retries:
                time.sleep(REQUEST_DELAY * attempt)
    return None

# ── Category detection ────────────────────────────────────────

def detect_category(slug: str, title: str, section: str) -> str:
    """Map a listing section + slug/title to the actual DB enum value."""
    fixed = LISTING_SECTIONS.get(section)
    if fixed:
        return fixed

    combined = (slug + " " + title).lower().replace("-", " ")
    for pattern, cat in _ORG_CATEGORY_RULES:
        if re.search(pattern, combined):
            return cat
    return "central_government"

# ── Organization ──────────────────────────────────────────────

def extract_org(slug: str, title: str) -> str:
    _ORG_NAMES = [
        (r"rrb|rrc", "Railway Recruitment Board (RRB)"),
        (r"railway|irctc", "Indian Railways"),
        (r"\bssc\b", "Staff Selection Commission (SSC)"),
        (r"upsc", "UPSC"),
        (r"uppsc", "UPPSC"),
        (r"bpsc", "BPSC"),
        (r"rpsc", "RPSC"),
        (r"mpsc", "MPSC"),
        (r"nta\b", "National Testing Agency (NTA)"),
        (r"aiims", "AIIMS"),
        (r"ibps", "IBPS"),
        (r"\bsbi\b", "State Bank of India (SBI)"),
        (r"\brbi\b", "Reserve Bank of India (RBI)"),
        (r"\bbsf\b", "BSF"),
        (r"\bcrpf\b", "CRPF"),
        (r"\bcisf\b", "CISF"),
        (r"drdo", "DRDO"),
        (r"isro", "ISRO"),
        (r"barc", "BARC"),
        (r"india.post|postal", "India Post"),
        (r"upsssc", "UPSSSC"),
        (r"dsssb", "DSSSB"),
        (r"kvs\b", "Kendriya Vidyalaya Sangathan (KVS)"),
        (r"nvs\b", "Navodaya Vidyalaya Samiti (NVS)"),
    ]
    s = (slug + " " + title).lower()
    for pat, name in _ORG_NAMES:
        if re.search(pat, s):
            return name
    words = title.split()
    stop = {"recruitment", "online", "exam", "result", "admit", "answer", "syllabus",
            "form", "notification", "vacancy", "bharti", "application"}
    org_words = [w for w in words[:6] if w.lower() not in stop]
    return " ".join(org_words[:4]) if org_words else "Government of India"

# ── Text ──────────────────────────────────────────────────────

def clean(text: str) -> str:
    t = text.replace("\u200b", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", t).strip()

def is_noise_div(tag: Tag) -> bool:
    if tag.name not in ("div", "section", "aside", "p"):
        return False
    txt = tag.get_text()
    return bool(re.search(r"join\s+our\s+(whatsapp|telegram)", txt, re.I))

# ── Link table parsing (2-column label|url tables) ────────────

def parse_links_table(soup: BeautifulSoup) -> list[dict]:
    """
    The site uses 2-column tables: col1=label, col2=<a href>Click Here</a>
    This function extracts all such links with their proper labels.
    """
    links, seen = [], set()

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        # Check if this looks like a links table
        has_links = any(row.find("a", href=True) for row in rows)
        if not has_links:
            continue

        for idx, row in enumerate(rows):
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            label = clean(cells[0].get_text())
            if not label or len(label) < 3:
                continue
            if label.lower() in _SKIP_LINK_LABELS:
                continue
            # Skip noise rows
            if re.search(r"whatsapp|telegram|follow now|discover more", label, re.I):
                continue

            a_tag = cells[1].find("a", href=True)
            if not a_tag:
                continue
            href = a_tag["href"].strip()
            if not href or href.startswith(("#", "javascript")):
                continue
            domain = urlparse(href).netloc.lower()
            if any(s in domain for s in _SKIP_LINK_DOMAINS):
                continue
            if href in seen:
                continue
            seen.add(href)

            ll = label.lower()
            if any(w in ll for w in ["apply online", "apply now", "application form", "register"]):
                t = "apply_online"
            elif any(w in ll for w in ["notification", "advt", "advertisement", "short notice", "official notice"]):
                t = "notification"
            elif any(w in ll for w in ["admit card", "hall ticket", "e-admit"]):
                t = "admit_card"
            elif any(w in ll for w in ["result", "merit list", "score card", "final result"]):
                t = "result"
            elif any(w in ll for w in ["answer key", "answer sheet", "objection"]):
                t = "answer_key"
            elif any(w in ll for w in ["syllabus", "exam pattern", "curriculum"]):
                t = "syllabus"
            elif any(w in ll for w in ["official website", "official site", "home page", "homepage"]):
                t = "official_website"
            else:
                t = "download"

            links.append({"label": label, "url": href, "type": t, "sort_order": idx})

    return links

def first_link(links: list[dict], link_type: str) -> Optional[str]:
    for l in links:
        if l["type"] == link_type:
            return l["url"]
    return None

# ── Date parsing ──────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

def parse_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    for pat in [
        r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})",
        r"(\d{1,2})\s+([A-Za-z]+)[,.\s]+(\d{4})",
        r"([A-Za-z]+)\s+(\d{1,2})[,.\s]+(\d{4})",
    ]:
        m = re.search(pat, raw)
        if not m:
            continue
        g = m.groups()
        try:
            if g[0].isdigit() and g[1].isdigit():
                d, mo, y = int(g[0]), int(g[1]), int(g[2])
                return f"{y:04d}-{mo:02d}-{d:02d}"
            elif g[0].isdigit():
                day, mon_s, year = int(g[0]), g[1].lower()[:3], int(g[2])
                mo = _MONTHS.get(mon_s)
                if mo and 1 <= day <= 31:
                    return f"{year:04d}-{mo:02d}-{day:02d}"
            else:
                mon_s, day, year = g[0].lower()[:3], int(g[1]), int(g[2])
                mo = _MONTHS.get(mon_s)
                if mo and 1 <= day <= 31:
                    return f"{year:04d}-{mo:02d}-{day:02d}"
        except Exception:
            pass
    return None

def extract_dates_from_page(soup: BeautifulSoup) -> dict:
    """
    Extract key dates from all text on the page.
    Works with both traditional tables and Q&A format.
    """
    out = {
        "application_start_date": None,
        "application_end_date": None,
        "exam_date": None,
        "admit_card_date": None,
        "result_date": None,
        "notification_date": None,
    }

    # Collect all text blocks that contain a date
    date_blocks = []
    for tag in soup.find_all(["tr", "td", "p", "li", "div"]):
        children = list(tag.children)
        # Skip deeply nested (we want leaf-ish elements)
        if sum(1 for c in children if isinstance(c, Tag)) > 4:
            continue
        t = clean(tag.get_text(" "))
        if not re.search(r"\d{1,2}\s+[A-Za-z]+\s+\d{4}", t):
            continue
        if len(t) > 600:
            continue
        date_blocks.append(t)

    # Also try traditional 2-col key-value tables
    for table in soup.find_all("table"):
        tl = table.get_text().lower()
        if not any(w in tl for w in ["date", "start", "last", "exam", "result"]):
            continue
        for row in table.find_all("tr"):
            cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
            if len(cells) >= 2 and cells[0] and len(cells[0]) < 120:
                date_blocks.append(f"{cells[0]}: {cells[1]}")

    for block in date_blocks:
        tl = block.lower()
        d = parse_date(block)
        if not d:
            continue
        if any(w in tl for w in ["apply start", "application start", "start date",
                                   "start on", "started on", "begin", "opening"]):
            if not out["application_start_date"]:
                out["application_start_date"] = d
        elif any(w in tl for w in ["last date", "closing date", "end date",
                                    "apply last", "deadline", "last day", "close"]):
            if not out["application_end_date"]:
                out["application_end_date"] = d
        elif any(w in tl for w in ["admit card", "hall ticket", "e-admit"]):
            if not out["admit_card_date"]:
                out["admit_card_date"] = d
        elif "result" in tl and "date" in tl:
            if not out["result_date"]:
                out["result_date"] = d
        elif any(w in tl for w in ["exam date", "exam schedule", "exam will",
                                    "cbt date", "written test", "cbat"]):
            if not out["exam_date"]:
                out["exam_date"] = d
        elif any(w in tl for w in ["notification", "notification date", "advt date"]):
            if not out["notification_date"]:
                out["notification_date"] = d

    return out

# ── Fee parsing ───────────────────────────────────────────────

def parse_fee_amount(text: str) -> Optional[float]:
    t = re.sub(r"[₹Rs./\-]", "", text)
    m = re.search(r"[\d,]+", t)
    if m:
        try:
            return float(m.group().replace(",", ""))
        except Exception:
            pass
    return None

def extract_fees(soup: BeautifulSoup) -> dict:
    out = {"fee_general": None, "fee_obc": None, "fee_sc_st": None, "fee_female": None}
    for table in soup.find_all("table"):
        if "fee" not in table.get_text().lower():
            continue
        for row in table.find_all("tr"):
            cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
            if len(cells) < 2 or not cells[0]:
                continue
            kl = cells[0].lower()
            amt = parse_fee_amount(cells[1])
            if amt is None:
                continue
            if "general" in kl or "ur" in kl:
                if out["fee_general"] is None:
                    out["fee_general"] = amt
            if "obc" in kl:
                if out["fee_obc"] is None:
                    out["fee_obc"] = amt
            if any(w in kl for w in ["sc", "st", "pwd", "ex"]):
                if out["fee_sc_st"] is None:
                    out["fee_sc_st"] = amt
            if any(w in kl for w in ["female", "women", "girl"]):
                if out["fee_female"] is None:
                    out["fee_female"] = amt
    return out

# ── Vacancy / age ─────────────────────────────────────────────

def parse_vacancy_table(soup: BeautifulSoup) -> list[dict]:
    rows = []
    for table in soup.find_all("table"):
        tl = table.get_text().lower()
        if not any(w in tl for w in ["vacancy", "post", "ur", "obc", "ews"]):
            continue
        hdrs = []
        for i, row in enumerate(table.find_all("tr")):
            cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
            if not any(cells):
                continue
            if i == 0 or not hdrs:
                hdrs = cells
            elif len(cells) == len(hdrs) and len(hdrs) <= 10:
                rows.append(dict(zip(hdrs, cells)))
    return rows

def parse_total_vacancies(rows: list[dict], title: str) -> Optional[int]:
    for row in rows:
        for k, v in row.items():
            if "total" in k.lower():
                for n in re.findall(r"[\d,]+", str(v)):
                    try:
                        return int(n.replace(",", ""))
                    except Exception:
                        pass
    m = re.search(r"\(([\d,]+)\s*posts?\)", title, re.I)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except Exception:
            pass
    return None

def extract_ages(soup: BeautifulSoup) -> tuple[Optional[int], Optional[int]]:
    min_age = max_age = None
    for tag in soup.find_all(["table", "p", "li"]):
        t = clean(tag.get_text())
        if "age" not in t.lower():
            continue
        nums = re.findall(r"\b(\d{2})\b", t)
        for n in map(int, nums):
            if 16 <= n <= 25 and not min_age:
                min_age = n
            elif 25 < n <= 55 and not max_age:
                max_age = n
    return min_age, max_age

# ── Selection / qualifications ────────────────────────────────

def parse_selection(soup: BeautifulSoup) -> list[str]:
    steps = []
    for table in soup.find_all("table"):
        tl = table.get_text().lower()
        if "selection" in tl or "mode of" in tl:
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) == 1:
                    t = clean(cells[0].get_text())
                    for step in re.split(r"[•\n]", t):
                        step = step.strip()
                        if 5 < len(step) < 150:
                            steps.append(step)
    if not steps:
        for ul in soup.find_all(["ul", "ol"]):
            prev = ul.find_previous(["h2", "h3", "h4", "strong", "b"])
            if prev and "selection" in prev.get_text().lower():
                for li in ul.find_all("li"):
                    t = clean(li.get_text())
                    if t and len(t) < 200:
                        steps.append(t)
    return steps[:10]

# ── Tags ──────────────────────────────────────────────────────

_TAG_RULES = [
    (r"rrb|rrc|railway", "Railway"), (r"\bssc\b", "SSC"),
    (r"upsc", "UPSC"), (r"uppsc", "UPPSC"), (r"bpsc", "BPSC"),
    (r"\bnta\b", "NTA"), (r"police", "Police"),
    (r"army|navy|airforce|defence|bsf|crpf|cisf", "Defence"),
    (r"teacher|teaching|tgt|pgt|tet|ctet|reet", "Teaching"),
    (r"bank|ibps|sbi|rbi|nabard", "Banking"),
    (r"engineer|jee|gate|technical|iti\b|diploma", "Engineering"),
    (r"medical|aiims|neet|doctor|nurse|health|drdo", "Medical/Defence PSU"),
    (r"10th|matric", "10th Pass"), (r"12th|inter", "12th Pass"),
    (r"graduate|degree|b\.?sc|b\.?a\.|b\.?com", "Graduate"),
]

def extract_tags(title: str, slug: str, category: str) -> list[str]:
    combined = (title + " " + slug).lower()
    tags = set()
    for pattern, tag in _TAG_RULES:
        if re.search(pattern, combined):
            tags.add(tag)
    cat_tag = {
        "central_government": "Central Govt", "state_government": "State Govt",
        "railway": "Railway", "ssc": "SSC", "upsc": "UPSC",
        "banking": "Banking", "police": "Police", "defence": "Defence",
        "teaching": "Teaching", "psu": "PSU", "admission": "Admission",
        "scholarship": "Scholarship", "result": "Result",
        "answer_key": "Answer Key", "admit_card": "Admit Card",
        "syllabus": "Syllabus", "other": "Other",
    }.get(category)
    if cat_tag:
        tags.add(cat_tag)
    return sorted(tags)

# ── Status ────────────────────────────────────────────────────

def compute_status(dates: dict) -> str:
    today = datetime.now(timezone.utc).date()
    end = dates.get("application_end_date")
    if end:
        try:
            if date.fromisoformat(end) >= today:
                return "open"
        except Exception:
            pass
    return "closed"

# ── Detail page ───────────────────────────────────────────────

def scrape_detail(url: str, category: str, listing_label: str = "") -> Optional[dict]:
    soup = fetch(url)
    if not soup:
        return None

    # Grab H1 BEFORE any noise removal
    h1 = soup.find("h1")
    title = clean(h1.get_text()) if h1 else ""

    # Remove noise elements
    for tag in soup.find_all(["script", "style", "iframe", "noscript", "nav"]):
        tag.decompose()
    for tag in list(soup.find_all(["div", "section", "aside", "p"])):
        if is_noise_div(tag):
            tag.decompose()
    for table in list(soup.find_all("table")):
        t = table.get_text().lower()
        if "join our whatsapp" in t or "join our telegram" in t:
            table.decompose()

    # Fallback title from listing label
    if not title or len(title) < 5:
        title = listing_label or url.split("/")[-2]

    slug = urlparse(url).path.strip("/").split("/")[-1]
    org = extract_org(slug, title)

    links = parse_links_table(soup)
    dates = extract_dates_from_page(soup)
    fees = extract_fees(soup)
    vacancies = parse_vacancy_table(soup)
    min_age, max_age = extract_ages(soup)
    selection = parse_selection(soup)
    total_vac = parse_total_vacancies(vacancies, title)
    tags = extract_tags(title, slug, category)

    # Short description: first meaningful paragraph
    short_desc = ""
    description = ""
    for p in soup.find_all(["p", "td"]):
        t = clean(p.get_text())
        if len(t) > 80 and not re.search(r"(disclaimer|copyright|follow|join)", t, re.I):
            if not short_desc:
                short_desc = t[:300]
            if not description:
                description = t
            else:
                break

    # Qualifications hint
    qualifications = []
    for tag in soup.find_all(["p", "li", "td"]):
        t = clean(tag.get_text())
        if any(w in t.lower() for w in ["10th", "12th", "graduate", "degree", "diploma", "iti"]):
            if 20 < len(t) < 400:
                qualifications.append(t)
                break

    status = compute_status(dates)

    return {
        "slug":                    slug,
        "source_url":              url,
        "title":                   title,
        "short_description":       short_desc,
        "description":             description,
        "organization":            org,
        "category":                category,
        "status":                  status,
        "total_vacancies":         total_vac,
        "vacancy_breakdown":       vacancies if vacancies else [],
        "min_age":                 min_age,
        "max_age":                 max_age,
        "fee_general":             fees["fee_general"],
        "fee_obc":                 fees["fee_obc"],
        "fee_sc_st":               fees["fee_sc_st"],
        "fee_female":              fees["fee_female"],
        "notification_date":       dates["notification_date"],
        "application_start_date":  dates["application_start_date"],
        "application_end_date":    dates["application_end_date"],
        "exam_date":               dates["exam_date"],
        "admit_card_date":         dates["admit_card_date"],
        "result_date":             dates["result_date"],
        "apply_url":               first_link(links, "apply_online"),
        "notification_pdf_url":    first_link(links, "notification"),
        "admit_card_url":          first_link(links, "admit_card"),
        "answer_key_url":          first_link(links, "answer_key"),
        "result_url":              first_link(links, "result"),
        "official_website":        first_link(links, "official_website"),
        "selection_process":       selection,
        "qualifications":          qualifications,
        "tags":                    tags,
        "is_featured":             False,
        "is_trending":             False,
        "_links":                  links,       # internal — removed before upsert
        "scraped_at":              datetime.now(timezone.utc).isoformat(),
    }

# ── Listing ───────────────────────────────────────────────────

def scrape_listing(section: str, url: str) -> list[dict]:
    log.info("Scanning: %s", url)
    soup = fetch(url)
    if not soup:
        return []
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        full = urljoin(BASE_URL, href)
        parsed = urlparse(full)
        if parsed.netloc.replace("www.", "") != "sarkariresult.com.cm":
            continue
        path_parts = [s for s in parsed.path.strip("/").split("/") if s]
        if not path_parts:
            continue
        slug = path_parts[-1]
        if slug in _SKIP_PATHS or len(path_parts) < 1:
            continue
        if not re.search(r"[a-z].*[0-9]|[0-9].*[a-z]", slug):
            continue   # skip non-post slugs (no mix of letters+digits)
        if full in seen:
            continue
        label = clean(a.get_text())
        if not label or len(label) < 8:
            continue
        seen.add(full)
        items.append({"url": full, "label": label, "slug": slug, "section": section})
    log.info("  → %d links found", len(items))
    return items

# ── Supabase upsert ───────────────────────────────────────────

def upsert(sb: Client, detail: dict) -> str:
    """Returns 'new', 'updated', or 'error'."""
    links = detail.pop("_links", [])
    try:
        # Clean row — only send columns that exist in opportunities table
        FIELDS = {
            "slug", "source_url", "title", "short_description", "description",
            "organization", "category", "status", "total_vacancies", "vacancy_breakdown",
            "min_age", "max_age", "fee_general", "fee_obc", "fee_sc_st", "fee_female",
            "notification_date", "application_start_date", "application_end_date",
            "exam_date", "admit_card_date", "result_date", "apply_url",
            "notification_pdf_url", "admit_card_url", "answer_key_url", "result_url",
            "official_website", "selection_process", "qualifications", "tags",
            "is_featured", "is_trending", "scraped_at",
        }
        row = {k: v for k, v in detail.items() if k in FIELDS and v is not None}
        # Ensure arrays are lists not None
        for arr_field in ["selection_process", "qualifications", "tags", "vacancy_breakdown"]:
            if arr_field not in row:
                row[arr_field] = []

        # Check if exists
        ex = sb.table("opportunities").select("id").eq("source_url", row["source_url"]).execute()
        is_new = not ex.data

        resp = sb.table("opportunities").upsert(row, on_conflict="source_url").execute()
        if not resp.data:
            log.error("Upsert returned no data for %s", row.get("source_url"))
            detail["_links"] = links
            return "error"

        opp_id = resp.data[0]["id"]

        # Save all links
        if links:
            try:
                sb.table("opportunity_links").delete().eq("opportunity_id", opp_id).execute()
                sb.table("opportunity_links").insert([
                    {"opportunity_id": opp_id, "label": l["label"],
                     "url": l["url"], "type": l["type"], "sort_order": l.get("sort_order", 0)}
                    for l in links
                ]).execute()
            except Exception as le:
                log.warning("Links insert failed for %s: %s", opp_id, le)

        detail["_links"] = links
        return "new" if is_new else "updated"

    except Exception as e:
        log.error("Upsert error %s — %s", detail.get("source_url"), e)
        detail["_links"] = links
        return "error"

# ── Local backup ──────────────────────────────────────────────

def load_local() -> dict:
    f = DATA_DIR / "all_items.json"
    if f.exists():
        try:
            data = json.loads(f.read_text())
            return {r["source_url"]: r for r in data if "source_url" in r}
        except Exception:
            pass
    return {}

def save_local(records: dict) -> None:
    f = DATA_DIR / "all_items.json"
    sorted_r = sorted(records.values(), key=lambda r: r.get("scraped_at", ""), reverse=True)
    f.write_text(json.dumps(sorted_r, ensure_ascii=False, indent=2, default=str))

# ── Run log ───────────────────────────────────────────────────

def log_start(sb) -> Optional[str]:
    if not sb:
        return None
    try:
        r = sb.table("scraper_runs").insert({"status": "running"}).execute()
        return r.data[0]["id"] if r.data else None
    except Exception:
        return None

def log_finish(sb, run_id, scraped, new, updated, errors, status):
    if not sb or not run_id:
        return
    try:
        sb.table("scraper_runs").update({
            "finished_at":   datetime.now(timezone.utc).isoformat(),
            "status":        status,
            "items_scraped": scraped,
            "items_new":     new,
            "items_updated": updated,
            "errors":        errors,
        }).eq("id", run_id).execute()
    except Exception:
        pass

# ── Main entry ────────────────────────────────────────────────

def run_scrape(max_per_category: int = 0) -> dict:
    sb = get_supabase()
    local = load_local()
    seen_urls: set = set(local.keys())

    if sb:
        try:
            r = sb.table("opportunities").select("source_url").not_.is_("source_url", "null").execute()
            for row in (r.data or []):
                seen_urls.add(row["source_url"])
            log.info("DB has %d existing records", len(r.data or []))
        except Exception as e:
            log.warning("Could not fetch existing URLs: %s", e)

    run_id = log_start(sb)
    new_count = updated_count = 0
    errors: list[dict] = []

    for section, listing_url in CATEGORY_URLS.items():
        items = scrape_listing(section, listing_url)
        if max_per_category and max_per_category > 0:
            items = items[:max_per_category]

        for item in items:
            url = item["url"]
            if url in seen_urls:
                continue

            log.info("Scraping [%s]: %s", section, url)
            time.sleep(REQUEST_DELAY)

            category = detect_category(item["slug"], item["label"], section)
            detail = scrape_detail(url, category, item["label"])
            if not detail:
                errors.append({"url": url, "error": "scrape_failed"})
                continue

            if sb:
                result = upsert(sb, detail)
                if result == "new":
                    new_count += 1
                    log.info("  ✓ [new] %s", detail["title"][:70])
                elif result == "updated":
                    updated_count += 1
                    log.info("  ✓ [upd] %s", detail["title"][:70])
                else:
                    errors.append({"url": url, "error": "upsert_failed"})
            else:
                new_count += 1

            seen_urls.add(url)
            local[url] = {k: v for k, v in detail.items() if k != "_links"}

        save_local(local)

    status = "success" if not errors else ("partial" if (new_count + updated_count) > 0 else "error")
    log_finish(sb, run_id, new_count + updated_count, new_count, updated_count, errors, status)

    result = {"new": new_count, "updated": updated_count, "errors": len(errors), "total": len(local)}
    log.info("Done — new:%d updated:%d errors:%d total_local:%d",
             new_count, updated_count, len(errors), len(local))
    return result


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="Max items per category (0=unlimited)")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=int, default=5)
    args = p.parse_args()

    if args.watch:
        import schedule
        log.info("Watch mode — every %d min", args.interval)
        def _job():
            try:
                run_scrape(args.limit)
            except Exception as exc:
                log.exception("Run failed: %s", exc)
        _job()
        schedule.every(args.interval).minutes.do(_job)
        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        run_scrape(args.limit)
