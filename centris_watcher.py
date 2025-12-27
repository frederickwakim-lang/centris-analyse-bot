from dotenv import load_dotenv
load_dotenv()  # Charge .env en local (sur Render, il ignore si pas là)

import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup

# ✅ Template 1 (nouveau)
from template1_calcs import Template1Inputs, compute_template1, format_discord_template1


# ===========================
# CONFIG
# ===========================

CENTRIS_SEARCH_URL = os.getenv(
    "CENTRIS_SEARCH_URL",
    "https://www.centris.ca/fr/plex~a-vendre?uc=0",
)

# ⚠️ IMPORTANT: ici on met la base URL (sans /analyze),
# puis on appelle /analyze nous-mêmes.
ANALYZER_BASE_URL = os.getenv(
    "ANALYZER_BASE_URL",
    "https://centris-analyse-bot.onrender.com",
).rstrip("/")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

REQUEST_INTERVAL_SECONDS = int(os.getenv("REQUEST_INTERVAL_SECONDS", "40"))
FULL_SCAN_INTERVAL_SECONDS = int(os.getenv("FULL_SCAN_INTERVAL_SECONDS", "300"))

SEEN_FILE = "seen_listings.json"


# ===========================
# UTILITAIRES
# ===========================

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
    """Télécharge une page (Centris) avec headers plus réalistes."""
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


# ===========================
# ANALYSE (HTML -> /analyze)
# ===========================

def analyze_listing(url: str):
    """
    ✅ NOUVEAU FLOW (anti N/A):
    1) Le watcher fetch le HTML complet
    2) Il envoie {"content": html} à l’analyseur
    3) L’analyseur parse et retourne un JSON
    """
    print(f"🧠 Analyse : {url}")

    try:
        html = fetch_html_from_url(url)
    except Exception as e:
        print(f"  ❌ Erreur fetch HTML : {e}")
        return None

    # Debug / protection contre pages bloquées
    html_len = len(html) if html else 0
    print(f"  📄 HTML length = {html_len}")

    # Si c’est trop petit, souvent c’est une page de blocage/placeholder
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


# ===========================
# MAPPING -> TEMPLATE 1
# ===========================

def build_template1_inputs(data: dict) -> Template1Inputs:
    """
    IMPORTANT:
    Ici on mappe les clés de TON analyseur (data) vers Template1Inputs.
    J’ai mis des clés probables + fallbacks.
    Si tes clés diffèrent, on ajustera en 30 sec avec un exemple JSON.
    """
    po = data.get("property_overview") or {}
    rev = data.get("revenus") or {}
    dep = data.get("depenses_vraies") or {}

    inp = Template1Inputs(
        price=po.get("prix") or data.get("price"),
        units=po.get("unites") or data.get("units"),
        revenu_brut_annuel=rev.get("revenu_brut_potentiel_annuel") or data.get("gross_income_annual"),

        taxes_scolaires=dep.get("taxes_scolaires") or data.get("taxes_school"),
        taxes_municipales=dep.get("taxes_municipales") or data.get("taxes_municipal"),

        assurances=dep.get("assurances") or data.get("insurance_annual"),
        services_publics=dep.get("services_publics") or data.get("utilities_annual"),
        electricite=dep.get("electricite") or data.get("electricity_annual"),
        chauffage=dep.get("chauffage") or data.get("heating_annual"),
        deneigement=dep.get("deneigement") or data.get("snow_annual"),
        conciergerie=dep.get("conciergerie") or data.get("concierge_annual"),
    )
    return inp


# ===========================
# DISCORD
# ===========================

def send_discord_message(data: dict, url: str):
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ Aucun webhook Discord configuré.")
        return

    # Si on a un blocage Centris détecté
    if isinstance(data, dict) and data.get("_error"):
        content = f"⚠️ **Annonce détectée mais HTML bloqué/incomplet** (len={data.get('_html_len')})\n{url}"
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
        print(f"  Discord status {resp.status_code}")
        return

    if not isinstance(data, dict):
        content = f"⚠️ **Analyse échouée**\n{url}"
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
        print(f"  Discord status {resp.status_code}")
        return

    # ✅ Template 1 calculations
    inp = build_template1_inputs(data)
    out = compute_template1(inp)
    content = format_discord_template1(url, inp, out)

    resp = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": content},
        timeout=30,
    )
    print(f"  Discord status {resp.status_code}")
    if resp.status_code not in (200, 204):
        print(f"  ⚠️ Réponse Discord : {resp.text[:300]}")


# ===========================
# UN CYCLE COMPLET DE SCAN
# ===========================

def run_one_full_scan():
    seen = load_seen_ids()
    print(f"📂 {len(seen)} annonces déjà analysées avant ce scan.\n")

    urls = get_listing_urls_from_search()

    for url in urls:
        listing_id = extract_listing_id(url)

        if not listing_id:
            print(f"🔸 Pas d'ID : {url}")
            continue

        if listing_id in seen:
            print(f"⏩ Déjà vue : {listing_id}")
            continue

        data = analyze_listing(url)

        if not isinstance(data, dict):
            print("❌ Erreur sur cette annonce, on passe à la suivante.\n")
        else:
            send_discord_message(data, url)
            seen.add(listing_id)
            save_seen_ids(seen)

        print(f"⏳ Pause {REQUEST_INTERVAL_SECONDS} sec avant la prochaine annonce…\n")
        try:
            time.sleep(REQUEST_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("⛔ Arrêt manuel pendant le scan.")
            raise


# ===========================
# BOUCLE INFINIE POUR RENDER
# ===========================

def main_loop():
    while True:
        print("🚀 Nouveau scan complet Centris…")
        try:
            run_one_full_scan()
        except KeyboardInterrupt:
            print("⛔ Arrêt manuel demandé. On quitte proprement.")
            break
        except Exception as e:
            print(f"💥 Erreur au niveau du cycle : {e}")

        print(f"🕒 Pause {FULL_SCAN_INTERVAL_SECONDS} sec avant le prochain scan complet…\n")
        try:
            time.sleep(FULL_SCAN_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("⛔ Arrêt manuel pendant la pause. On quitte.")
            break


if __name__ == "__main__":
    main_loop()
