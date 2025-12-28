from dotenv import load_dotenv
load_dotenv()

import os
import json
import time
import re
import requests
import traceback
from bs4 import BeautifulSoup

from template1_calcs import Template1Inputs, compute_template1, format_discord_template1

CENTRIS_SEARCH_URL = os.getenv("CENTRIS_SEARCH_URL", "https://www.centris.ca/fr/plex~a-vendre?uc=0")
ANALYZER_BASE_URL = os.getenv("ANALYZER_BASE_URL", "https://centris-analyse-bot.onrender.com").rstrip("/")
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
    print(f"🔎 Téléchargement : {CENTRIS_SEARCH_URL}", flush=True)
    resp = requests.get(CENTRIS_SEARCH_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
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

        urls.append("https://www.centris.ca" + href)

    urls = list(dict.fromkeys(urls))
    print(f"➡️ {len(urls)} URLs trouvées", flush=True)
    return urls


def analyze_listing(url: str):
    print(f"🧠 Analyse : {url}", flush=True)

    try:
        html = fetch_html_from_url(url)
    except Exception as e:
        print(f"  ❌ Erreur fetch HTML : {e}", flush=True)
        return None

    html_len = len(html) if html else 0
    print(f"  📄 HTML length = {html_len}", flush=True)

    if not html or html_len < 50000:
        print("  ⚠️ HTML suspect (trop petit). Probable blocage Centris.", flush=True)
        return {"_error": "HTML suspect / blocage Centris", "_html_len": html_len}

    try:
        resp = requests.post(
            f"{ANALYZER_BASE_URL}/analyze",
            json={"content": html},
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
    except Exception as e:
        print(f"  ❌ Erreur réseau vers analyseur : {e}", flush=True)
        return None

    if resp.status_code != 200:
        print(f"  ❌ Erreur HTTP {resp.status_code} : {resp.text[:300]}", flush=True)
        return None

    try:
        data = resp.json()
    except Exception:
        print("  ❌ Réponse non JSON :", flush=True)
        print(resp.text[:500], flush=True)
        return None

    print("  ✅ Analyse OK", flush=True)
    return data


# ✅ FIX: no nested def -> avoids indentation issues on Render
def pick(d: dict, *paths, default=None):
    for path in paths:
        cur = d
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and cur not in (None, "", "N/A"):
            return cur
    return default


def build_template1_inputs(data: dict) -> Template1Inputs:
    price = pick(data, ("property_overview", "prix"))
    units = pick(data, ("property_overview", "nb_logements"))
    revenu_brut_annuel = pick(data, ("revenus", "revenu_brut_potentiel_annuel"))
    taxes_scolaires = pick(data, ("depenses_vraies", "taxes_scolaires"))
    taxes_municipales = pick(data, ("depenses_vraies", "taxes_municipales"))

    return Template1Inputs(
        price=price,
        units=units,
        revenu_brut_annuel=revenu_brut_annuel,
        taxes_scolaires=taxes_scolaires,
        taxes_municipales=taxes_municipales,
        assurances=None,
        services_publics=None,
        electricite=None,
        chauffage=None,
        deneigement=None,
        conciergerie=None,
    )


def send_discord_message(data: dict, url: str):
    if not DISCORD_WEBHOOK_URL:
        return

    if isinstance(data, dict) and data.get("_error"):
        content = f"⚠️ **HTML bloqué/incomplet** (len={data.get('_html_len')})\n{url}"
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
        return

    inp = build_template1_inputs(data)
    out = compute_template1(inp)
    content = format_discord_template1(url, inp, out)
    requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)


def run_one_full_scan():
    seen = load_seen_ids()
    print(f"📂 {len(seen)} annonces déjà analysées.", flush=True)

    for url in get_listing_urls_from_search():
        listing_id = extract_listing_id(url)
        if not listing_id or listing_id in seen:
            continue

        data = analyze_listing(url)
        if isinstance(data, dict):
            send_discord_message(data, url)
            seen.add(listing_id)
            save_seen_ids(seen)

        time.sleep(REQUEST_INTERVAL_SECONDS)


def main_loop():
    while True:
        run_one_full_scan()
        time.sleep(FULL_SCAN_INTERVAL_SECONDS)


# ✅ Render-safe: crash -> retry (won't become "Failed service")
if __name__ == "__main__":
    print("[WATCHER] starting…", flush=True)

    while True:
        try:
            main_loop()
        except KeyboardInterrupt:
            print("[WATCHER] stopped by user", flush=True)
            raise
        except Exception:
            print("[WATCHER] ERROR — restart in 60s", flush=True)
            traceback.print_exc()
            time.sleep(60)

print('WATCHER_VERSION=2025-12-27-2245', flush=True)

