import os
import json
import time
import re
import hashlib
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from bs4 import BeautifulSoup
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── sources ───────────────────────────────────────────────────────────────────

MEMO_PAGE     = "https://www.uscis.gov/laws-and-policy/policy-memoranda"
MEMO_RSS      = "https://www.uscis.gov/rss/policyalerts"
MEMO_GNEWS    = "https://news.google.com/rss/search?q=site:uscis.gov+%22policy+memorandum%22&hl=en-US&gl=US&ceid=US:en"

NEWS_PAGE     = "https://www.uscis.gov/newsroom/all-news"
NEWS_GNEWS    = "https://news.google.com/rss/search?q=site:uscis.gov+newsroom&hl=en-US&gl=US&ceid=US:en"

# Alert cutoff for news: June 6 2026
NEWS_CUTOFF   = datetime(2026, 6, 6)

TG_TOKEN      = os.environ["TELEGRAM_TOKEN"]
TG_CHAT       = os.environ["TELEGRAM_CHAT_ID"]
CHECK_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "5"))

MEMO_STATE    = "known_memos.json"
NEWS_STATE    = "known_news.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── helpers ───────────────────────────────────────────────────────────────────

def make_id(title):
    """Stable ID from title — immune to URL tracking param changes."""
    normalized = re.sub(r"[^a-z0-9]", "", title.lower())
    return hashlib.md5(normalized.encode()).hexdigest()[:16]


def is_june_2026(date_str):
    """True if date is in June 2026."""
    if not date_str:
        return False
    d = date_str.lower()
    return ("jun" in d and "2026" in d) or ("2026-06" in d)


def parse_date(date_str):
    """Try to parse a date string into a datetime, return None on failure."""
    if not date_str:
        return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str[:len(fmt)+6].strip(), fmt)
            return dt.replace(tzinfo=None)
        except Exception:
            continue
    # fallback: look for year/month
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", date_str)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y")
        except Exception:
            pass
    return None


def is_after_cutoff(date_str, cutoff):
    """True if date_str parses to a date after cutoff."""
    dt = parse_date(date_str)
    if dt is None:
        return False
    return dt >= cutoff


# ── state ─────────────────────────────────────────────────────────────────────

def load_known(path):
    if os.path.exists(path):
        with open(path) as f:
            return set(json.load(f))
    return set()

def save_known(ids, path):
    with open(path, "w") as f:
        json.dump(list(ids), f)


# ── fetchers ──────────────────────────────────────────────────────────────────

def fetch_rss(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns   = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link")  or "").strip()
        date  = (item.findtext("pubDate") or "").strip()
        if title and link:
            items.append({"id": make_id(title), "title": title, "url": link, "date": date})

    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        link  = entry.find("atom:link", ns)
        href  = link.get("href", "") if link is not None else ""
        date  = (entry.findtext("atom:updated", namespaces=ns) or "").strip()
        if title and href:
            items.append({"id": make_id(title), "title": title, "url": href, "date": date})

    return items


def fetch_scrape_memos():
    r = requests.get(MEMO_PAGE, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if len(text) < 15:
            continue
        if any(kw in href.lower() for kw in ["memo","pm-602","/document/","policy-alert"]) or \
           any(kw in text.lower() for kw in ["memorandum","policy alert","hold and review"]):
            full = href if href.startswith("http") else "https://www.uscis.gov" + href
            items.append({"id": make_id(text), "title": text, "url": full, "date": ""})
    seen, unique = set(), []
    for m in items:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique[:30]


def fetch_scrape_news():
    r = requests.get(NEWS_PAGE, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup  = BeautifulSoup(r.text, "lxml")
    items = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if len(text) < 20:
            continue
        if "/newsroom/" in href or "/news/" in href:
            full = href if href.startswith("http") else "https://www.uscis.gov" + href
            # try to find a nearby date tag
            parent = a.find_parent()
            date = ""
            if parent:
                time_tag = parent.find("time")
                if time_tag:
                    date = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
            items.append({"id": make_id(text), "title": text, "url": full, "date": date})
    seen, unique = set(), []
    for m in items:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique[:50]


def fetch_memos():
    for name, fn in [
        ("USCIS RSS",       lambda: fetch_rss(MEMO_RSS)),
        ("Google News RSS", lambda: fetch_rss(MEMO_GNEWS)),
        ("Direct scrape",   fetch_scrape_memos),
    ]:
        try:
            items = fn()
            if items:
                log.info("[MEMOS] Fetched %d via %s", len(items), name)
                return items
            log.warning("[MEMOS] %s returned 0 items", name)
        except Exception as e:
            log.warning("[MEMOS] %s failed: %s", name, e)
    return []


def fetch_news():
    for name, fn in [
        ("Google News RSS", lambda: fetch_rss(NEWS_GNEWS)),
        ("Direct scrape",   fetch_scrape_news),
    ]:
        try:
            items = fn()
            if items:
                log.info("[NEWS] Fetched %d via %s", len(items), name)
                return items
            log.warning("[NEWS] %s returned 0 items", name)
        except Exception as e:
            log.warning("[NEWS] %s failed: %s", name, e)
    return []


# ── telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text):
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = {"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
            "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=data, timeout=10)
        if r.ok:
            log.info("Telegram sent OK")
        else:
            log.error("Telegram error %s: %s", r.status_code, r.text)
        return r.ok
    except Exception as e:
        log.error("Telegram exception: %s", e)
        return False


# ── check functions ───────────────────────────────────────────────────────────

def check_memos():
    items = fetch_memos()
    if not items:
        log.warning("[MEMOS] No items found")
        return

    known     = load_known(MEMO_STATE)
    first_run = len(known) == 0
    new_items = [m for m in items if m["id"] not in known]

    if first_run:
        log.info("[MEMOS] First run — saving %d as baseline", len(items))
        save_known({m["id"] for m in items}, MEMO_STATE)
        return

    log.info("[MEMOS] %d found, %d new", len(items), len(new_items))

    filtered = [m for m in new_items if is_june_2026(m.get("date", ""))]
    log.info("[MEMOS] %d pass June 2026 filter", len(filtered))

    for m in filtered:
        send_telegram(
            f"<b>New USCIS Policy Memorandum</b>\n\n"
            f"<b>{m['title']}</b>\n"
            + (f"Published: {m['date']}\n" if m['date'] else "")
            + f"\n<a href='{m['url']}'>Read memo</a>"
        )
        time.sleep(1)

    if new_items:
        known.update(m["id"] for m in new_items)
        save_known(known, MEMO_STATE)


def check_news():
    items = fetch_news()
    if not items:
        log.warning("[NEWS] No items found")
        return

    known     = load_known(NEWS_STATE)
    first_run = len(known) == 0
    new_items = [m for m in items if m["id"] not in known]

    if first_run:
        log.info("[NEWS] First run — saving %d as baseline", len(items))
        save_known({m["id"] for m in items}, NEWS_STATE)
        return

    log.info("[NEWS] %d found, %d new", len(items), len(new_items))

    filtered = [m for m in new_items if is_after_cutoff(m.get("date", ""), NEWS_CUTOFF)]
    log.info("[NEWS] %d pass June 6 2026 cutoff filter", len(filtered))

    for m in filtered:
        send_telegram(
            f"<b>New USCIS News</b>\n\n"
            f"<b>{m['title']}</b>\n"
            + (f"Published: {m['date']}\n" if m['date'] else "")
            + f"\n<a href='{m['url']}'>Read article</a>"
        )
        time.sleep(1)

    if new_items:
        known.update(m["id"] for m in new_items)
        save_known(known, NEWS_STATE)


# ── main ──────────────────────────────────────────────────────────────────────

def check():
    log.info("===== Checking USCIS =====")
    check_memos()
    check_news()


def main():
    log.info("USCIS Monitor starting (interval: %d min)", CHECK_MINUTES)
    send_telegram(
        f"USCIS Monitor started.\n\n"
        f"Monitoring:\n"
        f"• Policy Memoranda (June 2026 only)\n"
        f"• All News (after June 6, 2026)\n\n"
        f"Check interval: every {CHECK_MINUTES} min"
    )
    check()
    scheduler = BlockingScheduler()
    scheduler.add_job(check, "interval", minutes=CHECK_MINUTES)
    log.info("Scheduler running — press Ctrl+C to stop")
    scheduler.start()


if __name__ == "__main__":
    main()
