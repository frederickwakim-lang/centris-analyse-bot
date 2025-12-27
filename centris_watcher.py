from dotenv import load_dotenv
load_dotenv()  # Charge .env en local (sur Render, il ignore si pas là)

import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup

from template1_calcs import Template1Inputs, compute_template1, format_discord_template1


CENTRIS_SEARCH_URL = os.getenv(
    "CENTRIS_SEARCH_URL",
    "https://www.centris.ca/fr/plex~a-vendre?uc=0",
)

ANALYZER_BASE_URL = os.getenv(
    "ANALYZER_BASE_URL",
    "https://centris-analyse-bot.onrender.com",
).rstrip("/")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

REQUEST_INTERVAL_SECONDS = int(os.getenv("REQUEST_INTERVAL_SECONDS", "40"))
FULL_SCAN_INTERVAL_SECONDS = int(os.getenv("FULL_SCAN_INTERVAL_SECONDS", "300"))

SEEN_FILE = "seen_listings.json"


def load_seen_ids():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_seen_ids(ids_set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(ids_set)), f, ensure_ascii=False, indent=2)


def extract_listing_id(url: str):
    m = re.search(r"/(\d{7,8})(?:[^\d]|$)", url)
    return m.group(1) if m else None


def fetch_html_from_url(url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def get_listing_urls_from_search():
    print(f"🔎 Téléchargement : {CENTRIS_SEARCH_URL}")
    resp = requests.get(
        CENTRIS_SEARCH_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "plexes-for-sale" in href or "view=Map" in href:
            continue

        if not href.startswith("/fr/"):
            continue
        if not any(x in href for x in ["/duplex", "/triplex", "/quadruplex", "/plex"]):
            continue

        full_url = "https://www.centris.ca" + href
        urls.append(full_url)

    urls = list(dict.fromkeys(urls))
    print(f"➡️ {len(urls)} URLs trouvées")
    return urls


def analyze_listing(url: str):
    print(f"🧠 Analyse : {url}")

    try:
        html = fetch_html_from_url(url)
    except Exception as e:
        print(f"  ❌ Erreur fetch HTML : {e}")
        return None

    html_len = len(html) if html else 0
    print(f"  📄 HTML length = {html_len}")

    if not html or html_len < 50000:
        print("  ⚠️ HTML suspect (trop petit). Probable blocage Centris.")
        return {"_error": "HTML suspect / blocage Centris", "_html_len": html_len}

    try:
        resp = requests.post(
            f"{ANALYZER_BASE_URL}/analyze",
            json={"content": html},
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
    except Exception as e:
        print(f"  ❌ Erreur réseau vers analyseur : {e}")
        return None

    if resp.status_code != 200:
        print(f"  ❌ Erreur HTTP {resp.status_code} : {resp.text[:300]}")
        return None

    try:
        data = resp.json()
    except Exception:
        print("  ❌ Réponse non JSON :")
        print(resp.text[:500])
        return None

    print("  ✅ Analyse OK")
    return data


# ========= MAPPING ROBUSTE =========

def pick(d: dict, *paths, default=None):
    def get_path(obj, path):
