import os
import json
import time
import hashlib
import logging
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime
from apscheduler.schedulers.blocking import BlockingScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

USCIS_PAGE    = "https://www.uscis.gov/laws-and-policy/policy-memoranda"
USCIS_RSS     = "https://www.uscis.gov/rss/policyalerts"
GNEWS_RSS     = "https://news.google.com/rss/search?q=site:uscis.gov+%22policy+memorandum%22&hl=en-US&gl=US&ceid=US:en"

TG_TOKEN      = os.environ["TELEGRAM_TOKEN"]
TG_CHAT       = os.environ["TELEGRAM_CHAT_ID"]
CHECK_MINUTES = int(os.environ.get("CHECK_INTERVAL_MINUTES", "5"))
STATE_FILE    = "known_memos.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── state ────────────────────────────────────────────────────────────────────

def load_known():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f))
    return set()

def save_known(ids):
    with open(STATE_FILE, "w") as f:
        json.dump(list(ids), f)


# ── fetchers ─────────────────────────────────────────────────────────────────

def fetch_via_rss(url):
    """Parse an RSS/Atom feed and return list of {id, title, url, date}."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns   = {"atom": "http://www.w3.org/2005/Atom"}
    items = []

    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link  = (item.findtext("link")  or "").strip()
        date  = (item.findtext("pubDate") or "").strip()
        if title and link:
            items.append({
                "id":    hashlib.md5(link.encode()).hexdigest()[:12],
                "title": title,
                "url":   link,
                "date":  date,
            })

    # Atom
    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        link  = entry.find("atom:link", ns)
        href  = link.get("href", "") if link is not None else ""
        date  = (entry.findtext("atom:updated", namespaces=ns) or "").strip()
        if title and href:
            items.append({
                "id":    hashlib.md5(href.encode()).hexdigest()[:12],
                "title": title,
                "url":   href,
                "date":  date,
            })

    return items


def fetch_via_scrape(url):
    """Fallback: scrape the USCIS page directly."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup  = BeautifulSoup(r.text, "lxml")
    items = []

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if len(text) < 15:
            continue
        is_memo_link = any(kw in href.lower() for kw in ["memo","pm-602","/document/","policy-alert"])
        is_memo_text = any(kw in text.lower() for kw in ["memorandum","policy alert","hold and review","adjudicative"])
        if is_memo_link or is_memo_text:
            full = href if href.startswith("http") else "https://www.uscis.gov" + href
            items.append({
                "id":    hashlib.md5(text.encode()).hexdigest()[:12],
                "title": text,
                "url":   full,
                "date":  "",
            })

    # deduplicate
    seen, unique = set(), []
    for m in items:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique[:30]


def fetch_memos():
    """Try USCIS RSS → Google News RSS → direct scrape."""
    for name, fn in [
        ("USCIS RSS",      lambda: fetch_via_rss(USCIS_RSS)),
        ("Google News RSS",lambda: fetch_via_rss(GNEWS_RSS)),
        ("Direct scrape",  lambda: fetch_via_scrape(USCIS_PAGE)),
    ]:
        try:
            items = fn()
            if items:
                log.info("Fetched %d memos via %s", len(items), name)
                return items
            log.warning("%s returned 0 items", name)
        except Exception as e:
            log.warning("%s failed: %s", name, e)
    return []


# ── telegram ─────────────────────────────────────────────────────────────────

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


# ── main loop ─────────────────────────────────────────────────────────────────

def check():
    log.info("--- Checking USCIS ---")
    memos = fetch_memos()

    if not memos:
        log.warning("No memos found — will retry next cycle")
        return

    known     = load_known()
    first_run = len(known) == 0
    new_memos = [m for m in memos if m["id"] not in known]

    if first_run:
        log.info("First run: saving %d memos as baseline (no alerts sent)", len(memos))
        save_known({m["id"] for m in memos})
        send_telegram(
            f"USCIS Monitor is running.\n"
            f"Baseline: {len(memos)} existing memo(s) recorded.\n"
            f"You will be alerted when new ones appear.\n\n"
            f"Check interval: every {CHECK_MINUTES} min\n"
            f"Source: {USCIS_PAGE}"
        )
        return

    log.info("%d memos found, %d are new", len(memos), len(new_memos))

    for m in new_memos:
        msg = (
            f"<b>New USCIS Policy Memorandum</b>\n\n"
            f"<b>{m['title']}</b>\n"
            + (f"Published: {m['date']}\n" if m['date'] else "")
            + f"\n<a href='{m['url']}'>Read memo</a>"
        )
        send_telegram(msg)
        time.sleep(1)

    if new_memos:
        known.update(m["id"] for m in new_memos)
        save_known(known)


def main():
    log.info("USCIS Monitor starting (interval: %d min)", CHECK_MINUTES)
    check()
    scheduler = BlockingScheduler()
    scheduler.add_job(check, "interval", minutes=CHECK_MINUTES)
    log.info("Scheduler running — press Ctrl+C to stop")
    scheduler.start()


if __name__ == "__main__":
    main()
