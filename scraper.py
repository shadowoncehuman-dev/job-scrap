"""
Sarkari Portal Scraper
Scrapes sarkariresult.com.cm and upserts into Supabase `opportunities` table.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag
from supabase import create_client, Client

# ── Config ────────────────────────────────────────────────────

BASE_URL = "https://sarkariresult.com.cm"

CATEGORY_URLS = {
    "latest_job": f"{BASE_URL}/latest-jobs/",
    "result":     f"{BASE_URL}/result/",
    "admit_card": f"{BASE_URL}/admit-card/",
    "answer_key": f"{BASE_URL}/answer-key/",
    "admission":  f"{BASE_URL}/admission/",
    "syllabus":   f"{BASE_URL}/syllabus/",
}

_SKIP_PATHS = {
    "", "/", "/latest-jobs", "/latest-posts", "/result", "/admit-card",
    "/answer-key", "/admission", "/syllabus", "/contact", "/privacy-policy",
    "/disclaimer", "/sitemap", "/about", "/about-us", "/advertise",
    "/sarkari-result-2024",
}

_NOISE_RE = [
    re.compile(r"join\s+our\s+(whatsapp|telegram)\s+channel.*?follow\s+now", re.I | re.S),
    re.compile(r"download\s+sarkariresult\s+app\s+now", re.I),
    re.compile(r"discover\s+more", re.I),
    re.compile(r"follow\s+now", re.I),
]

_SKIP_LINK_DOMAINS = {
    "googleads", "doubleclick", "googlesyndication",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "t.me", "telegram.me", "whatsapp.com",
    "play.google.com", "apps.apple.com",
}
_SKIP_LINK_LABELS = {
    "join our whatsapp channel", "join our telegram channel",
    "follow now", "download sarkariresult app now",
    "whatsapp", "telegram", "sarkari result™",
    "sarkari result @x", "sarkari result @telegram",
    "sarkari result @whatsapp", "sarkari result @instagram",
    "sarkari result @facebook", "sarkari result @youtube",
    "sarkari result @mobile app",
}

# Organization patterns (slug prefix → display name)
_ORG_PATTERNS = [
    (r"^rrb", "Railway Recruitment Board (RRB)"),
    (r"^rrc", "Railway Recruitment Cell (RRC)"),
    (r"^railway", "Indian Railways"),
    (r"^ssc", "Staff Selection Commission (SSC)"),
    (r"^upsc", "Union Public Service Commission (UPSC)"),
    (r"^uppsc", "UP Public Service Commission (UPPSC)"),
    (r"^bpsc", "Bihar Public Service Commission (BPSC)"),
    (r"^rpsc", "Rajasthan Public Service Commission (RPSC)"),
    (r"^mpsc", "Maharashtra Public Service Commission (MPSC)"),
    (r"^kpsc", "Karnataka Public Service Commission (KPSC)"),
    (r"^nta", "National Testing Agency (NTA)"),
    (r"^aiims", "AIIMS"),
    (r"^ibps", "IBPS"),
    (r"^sbi", "State Bank of India (SBI)"),
    (r"^rbi", "Reserve Bank of India (RBI)"),
    (r"^bsf", "Border Security Force (BSF)"),
    (r"^crpf", "CRPF"),
    (r"^cisf", "CISF"),
    (r"^itbp", "ITBP"),
    (r"^ssb", "SSB"),
    (r"^drdo", "DRDO"),
    (r"^isro", "ISRO"),
    (r"^barc", "BARC"),
    (r"^indian.airforce", "Indian Air Force"),
    (r"^indian.navy", "Indian Navy"),
    (r"^indian.army", "Indian Army"),
    (r"^india.post", "India Post"),
    (r"^upsssc", "UPSSSC"),
    (r"^dsssb", "DSSSB"),
    (r"^dda", "Delhi Development Authority (DDA)"),
    (r"^hssc", "Haryana Staff Selection Commission (HSSC)"),
    (r"^rssb", "Rajasthan Staff Selection Board (RSSB)"),
    (r"^mpesb", "Madhya Pradesh Employee Selection Board"),
    (r"^uphesc", "UP Higher Education Service Commission"),
    (r"^allahabad.high.court", "Allahabad High Court"),
    (r"^neet", "NTA NEET"),
    (r"^jee", "NTA JEE"),
    (r"^cuet", "NTA CUET"),
    (r"^ugc.net", "UGC NET"),
    (r"^nta.ugc", "UGC NET"),
    (r"^ofss", "OFSS Bihar"),
    (r"^bhu", "Banaras Hindu University (BHU)"),
]

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "1.5"))

# ── Logging ───────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scraper")

# ── Supabase ──────────────────────────────────────────────────

def get_supabase() -> Optional[Client]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        log.warning("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — local JSON only")
        return None
    return create_client(url, key)

# ── HTTP ──────────────────────────────────────────────────────

_session = requests.Session()
_session.headers.update(HEADERS)

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

# ── Text helpers ──────────────────────────────────────────────

def clean(text: str) -> str:
    t = text.replace("\u200b", "").replace("\xa0", " ")
    for p in _NOISE_RE:
        t = p.sub("", t)
    return re.sub(r"\s+", " ", t).strip()

def is_noise_element(tag: Tag) -> bool:
    classes = " ".join(tag.get("class", []))
    combined = (classes + " " + tag.get("id", "")).lower()
    return any(p in combined for p in [
        "adsbygoogle", "advertisement", "banner", "doubleclick",
        "social-share", "whatsapp", "telegram-btn", "app-download",
        "footer-brand", "discover-more", "outbrain", "taboola",
    ])

# ── Organization ──────────────────────────────────────────────

def extract_org(slug: str, title: str) -> str:
    s = slug.lower().replace("_", "-")
    for pattern, name in _ORG_PATTERNS:
        if re.search(pattern, s):
            return name
    # Fallback: first word(s) of title before first verb/action word
    words = title.split()
    if words:
        org_words = []
        for w in words[:5]:
            if w.lower() in ("recruitment", "online", "exam", "result", "admit", "answer", "syllabus", "form", "notification"):
                break
            org_words.append(w)
        if org_words:
            return " ".join(org_words)
    return "Government of India"

# ── Fee parsing ───────────────────────────────────────────────

def parse_fee_amount(text: str) -> Optional[float]:
    m = re.search(r"[\d,]+", text.replace("₹", "").replace("Rs", "").replace("/-", ""))
    if m:
        try:
            return float(m.group().replace(",", ""))
        except Exception:
            pass
    return None

def extract_fees(fee_dict: dict) -> dict:
    out = {"fee_general": None, "fee_obc": None, "fee_sc_st": None, "fee_female": None}
    for k, v in fee_dict.items():
        kl = k.lower()
        amt = parse_fee_amount(str(v))
        if amt is None:
            continue
        if "general" in kl or "ur" in kl or "obc" in kl and "sc" not in kl:
            if "obc" in kl and out["fee_obc"] is None:
                out["fee_obc"] = amt
            elif out["fee_general"] is None:
                out["fee_general"] = amt
        if any(w in kl for w in ["sc", "st", "pwd", "ex-service", "pwbd"]):
            if out["fee_sc_st"] is None:
                out["fee_sc_st"] = amt
        if "female" in kl or "women" in kl or "girl" in kl:
            if out["fee_female"] is None:
                out["fee_female"] = amt
    return out

# ── Age parsing ───────────────────────────────────────────────

def extract_ages(age_dict: dict) -> tuple[Optional[int], Optional[int]]:
    min_age = max_age = None
    for k, v in age_dict.items():
        kl = (k + " " + str(v)).lower()
        nums = re.findall(r"\b(\d{2})\b", kl)
        for n in nums:
            n = int(n)
            if 16 <= n <= 25 and ("min" in kl or "minimum" in kl):
                min_age = n
            elif 25 <= n <= 55 and ("max" in kl or "maximum" in kl):
                max_age = n
    return min_age, max_age

# ── Date parsing ──────────────────────────────────────────────

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

def parse_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    for pat in [
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})",
        r"([A-Za-z]+)\s+(\d{1,2})[,\s]+(\d{4})",
    ]:
        m = re.search(pat, raw)
        if m:
            g = m.groups()
            if g[0].isdigit():
                day, mon_str, year = int(g[0]), g[1].lower()[:3], int(g[2])
            else:
                mon_str, day, year = g[0].lower()[:3], int(g[1]), int(g[2])
            month = _MONTHS.get(mon_str)
            if month and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
    return None

def extract_dates(important_dates: dict) -> dict:
    out = {
        "application_start_date": None,
        "application_end_date": None,
        "exam_date": None,
        "admit_card_date": None,
        "result_date": None,
        "notification_date": None,
    }
    for k, v in important_dates.items():
        kl = k.lower()
        d = parse_date(str(v))
        if not d:
            continue
        if any(w in kl for w in ["start", "apply start", "online apply start"]):
            out["application_start_date"] = d
        elif any(w in kl for w in ["last date", "end date", "apply last", "closing"]):
            out["application_end_date"] = d
        elif any(w in kl for w in ["exam date", "exam schedule", "cbt", "written"]):
            if not out["exam_date"]:
                out["exam_date"] = d
        elif any(w in kl for w in ["admit card", "hall ticket", "e-admit"]):
            if not out["admit_card_date"]:
                out["admit_card_date"] = d
        elif "result" in kl:
            if not out["result_date"]:
                out["result_date"] = d
        elif "notification" in kl or "official" in kl:
            if not out["notification_date"]:
                out["notification_date"] = d
    return out

# ── Tags ──────────────────────────────────────────────────────

_TAG_RULES = [
    (r"rrb|rrc|railway", "Railway"),
    (r"\bssc\b", "SSC"), (r"upsc", "UPSC"), (r"uppsc", "UPPSC"),
    (r"bpsc", "BPSC"), (r"rpsc", "RPSC"), (r"\bnta\b", "NTA"),
    (r"police", "Police"), (r"army|navy|airforce|defence|bsf|crpf|cisf", "Defence"),
    (r"teacher|teaching|tgt|pgt|tet|ctet|reet", "Teaching"),
    (r"bank|ibps|sbi|rbi|nabard", "Banking"),
    (r"engineer|jee|gate|technical", "Engineering"),
    (r"medical|aiims|neet|doctor|nurse|health", "Medical"),
    (r"admit card|hall ticket", "Admit Card"),
    (r"\bresult\b", "Result"),
    (r"answer key", "Answer Key"),
    (r"admission|entrance", "Admission"),
    (r"syllabus", "Syllabus"),
    (r"central govt|central government", "Central Govt"),
    (r"state govt|state government", "State Govt"),
    (r"10th|matric|ssc pass", "10th Pass"),
    (r"12th|inter|intermediate", "12th Pass"),
    (r"graduate|degree|b\.?sc|b\.?a\.|b\.?com", "Graduate"),
]

def extract_tags(title: str, slug: str, category: str) -> list[str]:
    combined = (title + " " + slug).lower()
    tags = set()
    for pattern, tag in _TAG_RULES:
        if re.search(pattern, combined):
            tags.add(tag)
    # Add category tag
    cat_tag = {
        "latest_job": "Latest Job", "result": "Result",
        "admit_card": "Admit Card", "answer_key": "Answer Key",
        "admission": "Admission", "syllabus": "Syllabus",
    }.get(category)
    if cat_tag:
        tags.add(cat_tag)
    return sorted(tags)

# ── Link extraction ───────────────────────────────────────────

def extract_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    links, seen = [], set()
    for i, a in enumerate(soup.find_all("a", href=True)):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript")):
            continue
        full_url = urljoin(base_url, href)
        domain = urlparse(full_url).netloc.lower()
        if full_url in seen:
            continue
        label = clean(a.get_text())
        if not label or len(label) < 3:
            continue
        if label.lower() in _SKIP_LINK_LABELS:
            continue
        if any(s in domain for s in _SKIP_LINK_DOMAINS):
            continue
        seen.add(full_url)
        ll = label.lower()
        if any(w in ll for w in ["apply", "application", "register", "form"]):
            t = "apply_online"
        elif any(w in ll for w in ["notification", "advt", "advertisement"]):
            t = "notification"
        elif any(w in ll for w in ["admit card", "hall ticket"]):
            t = "admit_card"
        elif any(w in ll for w in ["result", "merit list", "score card"]):
            t = "result"
        elif any(w in ll for w in ["answer key", "answer sheet"]):
            t = "answer_key"
        elif any(w in ll for w in ["syllabus", "exam pattern"]):
            t = "syllabus"
        elif any(w in ll for w in ["official website", "official site"]):
            t = "official_website"
        elif any(w in ll for w in ["download", "pdf", "notice"]):
            t = "download"
        else:
            t = "other"
        links.append({"label": label, "url": full_url, "type": t, "sort_order": i})
    return links

def first_link(links: list[dict], link_type: str) -> Optional[str]:
    for l in links:
        if l["type"] == link_type:
            return l["url"]
    return None

# ── Table parsers ─────────────────────────────────────────────

def parse_dates_table(soup: BeautifulSoup) -> dict:
    out = {}
    for table in soup.find_all("table"):
        tl = table.get_text().lower()
        if "important date" in tl or "start date" in tl or "last date" in tl:
            for row in table.find_all("tr"):
                cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2 and cells[0] and len(cells[0]) < 120:
                    out[cells[0]] = cells[1]
    return out

def parse_fee_table(soup: BeautifulSoup) -> dict:
    out = {}
    for table in soup.find_all("table"):
        tl = table.get_text().lower()
        if "fee" in tl or "payment" in tl:
            for row in table.find_all("tr"):
                cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2 and cells[0] and len(cells[0]) < 120:
                    out[cells[0]] = cells[1]
    return out

def parse_vacancy_table(soup: BeautifulSoup) -> list[dict]:
    rows = []
    for table in soup.find_all("table"):
        tl = table.get_text().lower()
        if any(w in tl for w in ["vacancy", "ur", "obc", "sc", "st", "ews"]):
            hdrs = []
            for i, row in enumerate(table.find_all("tr")):
                cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
                if not any(cells):
                    continue
                if i == 0 or not hdrs:
                    hdrs = cells
                elif len(cells) == len(hdrs) and len(hdrs) <= 8:
                    combined = " ".join(cells).lower()
                    if any(w in combined for w in ["ur", "obc", "sc", "st", "ews", "general"]):
                        rows.append(dict(zip(hdrs, cells)))
    return rows

def parse_age_table(soup: BeautifulSoup) -> dict:
    out = {}
    for table in soup.find_all("table"):
        if "age" in table.get_text().lower():
            for row in table.find_all("tr"):
                cells = [clean(td.get_text()) for td in row.find_all(["td", "th"])]
                if len(cells) >= 2 and cells[0] and len(cells[0]) < 80:
                    out[cells[0]] = cells[1]
    if not out:
        for tag in soup.find_all(["p", "li"]):
            t = clean(tag.get_text())
            if "age" in t.lower() and "year" in t.lower() and len(t) < 300:
                out["summary"] = t
                break
    return out

def parse_selection(soup: BeautifulSoup) -> list[str]:
    steps = []
    for ul in soup.find_all(["ul", "ol"]):
        prev = ul.find_previous(["h2", "h3", "h4", "p", "strong"])
        pt = prev.get_text().lower() if prev else ""
        if any(w in pt for w in ["selection", "mode of", "process"]):
            for li in ul.find_all("li"):
                t = clean(li.get_text())
                if t and len(t) < 200:
                    steps.append(t)
    return steps

def parse_total_vacancies(rows: list[dict], title: str) -> Optional[int]:
    total = 0
    found = False
    for row in rows:
        for k, v in row.items():
            if any(w in k.lower() for w in ["post", "vacancy", "no."]):
                for n in re.findall(r"[\d,]+", str(v)):
                    try:
                        total += int(n.replace(",", ""))
                        found = True
                    except Exception:
                        pass
    if found and total > 0:
        return total
    m = re.search(r"\(([\d,]+)\s+posts?\)", title, re.I)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except Exception:
            pass
    return None

# ── Status computation ────────────────────────────────────────

def compute_status(dates: dict) -> str:
    now = datetime.now(timezone.utc).date()
    end = dates.get("application_end_date")
    result = dates.get("result_date")
    if end:
        try:
            from datetime import date
            end_d = date.fromisoformat(end)
            if end_d >= now:
                return "open"
        except Exception:
            pass
    if result:
        return "result_declared"
    return "closed"

# ── Detail page ───────────────────────────────────────────────

def scrape_detail(url: str, category: str) -> Optional[dict]:
    soup = fetch(url)
    if not soup:
        return None

    for tag in soup.find_all(True):
        if is_noise_element(tag):
            tag.decompose()
    for tag in soup.find_all(["nav", "footer", "script", "style", "iframe", "noscript"]):
        tag.decompose()
    for tag in soup.find_all(["div", "p", "section"]):
        if re.search(r"join\s+our\s+(whatsapp|telegram)", tag.get_text(), re.I):
            tag.decompose()

    title = ""
    h1 = soup.find("h1")
    if h1:
        title = clean(h1.get_text())

    description = ""
    short_description = ""
    for p in soup.find_all("p"):
        t = clean(p.get_text())
        if len(t) > 80 and not re.search(r"(join|follow|channel|whatsapp|telegram|disclaimer)", t, re.I):
            if not short_description:
                short_description = t[:300]
            if not description:
                description = t
            else:
                break

    slug = urlparse(url).path.strip("/").split("/")[-1]
    org = extract_org(slug, title)
    important_dates = parse_dates_table(soup)
    fee_raw = parse_fee_table(soup)
    age_raw = parse_age_table(soup)
    vacancies = parse_vacancy_table(soup)
    all_links = extract_links(soup, url)
    dates = extract_dates(important_dates)
    fees = extract_fees(fee_raw)
    min_age, max_age = extract_ages(age_raw)
    status = compute_status(dates)
    total_vac = parse_total_vacancies(vacancies, title)
    tags = extract_tags(title, slug, category)
    selection = parse_selection(soup)

    # Extract qualification hints
    qualifications = []
    for tag in soup.find_all(["p", "li"]):
        t = clean(tag.get_text())
        if any(w in t.lower() for w in ["10th", "12th", "graduate", "degree", "diploma", "iti"]):
            if 20 < len(t) < 400:
                qualifications.append(t)
                break

    return {
        "slug":                  slug,
        "source_url":            url,
        "title":                 title or slug,
        "short_description":     short_description,
        "description":           description,
        "organization":          org,
        "category":              category,
        "status":                status,
        "total_vacancies":       total_vac,
        "vacancy_breakdown":     vacancies,
        "min_age":               min_age,
        "max_age":               max_age,
        "fee_general":           fees["fee_general"],
        "fee_obc":               fees["fee_obc"],
        "fee_sc_st":             fees["fee_sc_st"],
        "fee_female":            fees["fee_female"],
        "notification_date":     dates["notification_date"],
        "application_start_date": dates["application_start_date"],
        "application_end_date":  dates["application_end_date"],
        "exam_date":             dates["exam_date"],
        "admit_card_date":       dates["admit_card_date"],
        "result_date":           dates["result_date"],
        "apply_url":             first_link(all_links, "apply_online"),
        "notification_pdf_url":  first_link(all_links, "notification"),
        "admit_card_url":        first_link(all_links, "admit_card"),
        "answer_key_url":        first_link(all_links, "answer_key"),
        "result_url":            first_link(all_links, "result"),
        "official_website":      first_link(all_links, "official_website"),
        "selection_process":     selection,
        "qualifications":        qualifications,
        "tags":                  tags,
        "all_links":             all_links,
        "scraped_at":            datetime.now(timezone.utc).isoformat(),
    }

# ── Listing ───────────────────────────────────────────────────

def scrape_listing(category: str, url: str) -> list[dict]:
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
        if parsed.netloc not in ("sarkariresult.com.cm", "www.sarkariresult.com.cm"):
            continue
        path = parsed.path.rstrip("/")
        if path in _SKIP_PATHS:
            continue
        segs = [s for s in path.split("/") if s]
        if not segs:
            continue
        sl = segs[-1]
        if not re.search(r"\d{4}", sl) and "-" not in sl:
            continue
        if full in seen:
            continue
        label = clean(a.get_text())
        if not label or len(label) < 5:
            continue
        seen.add(full)
        items.append({"url": full, "label": label, "slug": sl})
    log.info("  Found %d links", len(items))
    return items

# ── Supabase write ────────────────────────────────────────────

def upsert(sb: Client, detail: dict) -> bool:
    all_links = detail.pop("all_links", [])
    try:
        row = {k: v for k, v in detail.items() if k != "all_links"}
        resp = sb.table("opportunities").upsert(row, on_conflict="source_url").execute()
        if not resp.data:
            log.error("Upsert no data: %s", detail.get("source_url"))
            detail["all_links"] = all_links
            return False
        opp_id = resp.data[0]["id"]

        if all_links:
            sb.table("opportunity_links").delete().eq("opportunity_id", opp_id).execute()
            sb.table("opportunity_links").insert([
                {"opportunity_id": opp_id, "label": l["label"],
                 "url": l["url"], "type": l["type"], "sort_order": l.get("sort_order", 0)}
                for l in all_links
            ]).execute()

        detail["all_links"] = all_links
        return True
    except Exception as e:
        log.error("Supabase error %s — %s", detail.get("source_url"), e)
        detail["all_links"] = all_links
        return False

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
    f.write_text(json.dumps(sorted_r, ensure_ascii=False, indent=2))

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
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status, "items_scraped": scraped,
            "items_new": new, "items_updated": updated, "errors": errors,
        }).eq("id", run_id).execute()
    except Exception:
        pass

# ── Main ──────────────────────────────────────────────────────

def run_scrape(max_per_category: int = 50) -> dict:
    sb = get_supabase()
    local = load_local()
    seen_urls = set(local.keys())

    if sb:
        try:
            r = sb.table("opportunities").select("source_url").execute()
            for row in (r.data or []):
                seen_urls.add(row["source_url"])
            log.info("DB has %d existing records", len(r.data or []))
        except Exception as e:
            log.warning("Could not fetch existing URLs: %s", e)

    run_id = log_start(sb)
    new_count = updated = 0
    errors: list[dict] = []

    for category, listing_url in CATEGORY_URLS.items():
        items = scrape_listing(category, listing_url)
        if max_per_category:
            items = items[:max_per_category]

        for item in items:
            url = item["url"]
            if url in seen_urls:
                continue

            log.info("Scraping: %s", url)
            time.sleep(REQUEST_DELAY)

            detail = scrape_detail(url, category)
            if not detail:
                errors.append({"url": url, "error": "scrape_failed"})
                continue

            if not detail.get("title") or detail["title"] == detail.get("slug"):
                detail["title"] = item["label"]
                detail["short_description"] = item["label"][:300]

            if sb:
                if upsert(sb, detail):
                    new_count += 1
                    log.info("  ✓ %s", detail["title"][:70])
                else:
                    errors.append({"url": url, "error": "upsert_failed"})
            else:
                new_count += 1

            seen_urls.add(url)
            local[url] = detail

        save_local(local)

    status = "success" if not errors else "partial"
    log_finish(sb, run_id, new_count + updated, new_count, updated, errors, status)

    result = {
        "new": new_count, "updated": updated,
        "errors": len(errors), "total": len(local),
    }
    log.info("Done — new:%d updated:%d errors:%d total:%d",
             new_count, updated, len(errors), len(local))
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=int, default=5)
    args = parser.parse_args()

    if args.watch:
        import schedule
        log.info("Watch mode — every %d min", args.interval)
        def _job():
            try:
                run_scrape(args.limit)
            except Exception as e:
                log.exception("Run failed: %s", e)
        _job()
        schedule.every(args.interval).minutes.do(_job)
        while True:
            schedule.run_pending()
            time.sleep(30)
    else:
        run_scrape(args.limit)
