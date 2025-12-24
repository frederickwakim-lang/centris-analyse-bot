from dotenv import load_dotenv
load_dotenv()  # Charge .env en local (sur Render, il ignore si pas là)

import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup

# ===========================
# CONFIG
# ===========================

CENTRIS_SEARCH_URL = os.getenv(
    "CENTRIS_SEARCH_URL",
    "https://www.centris.ca/fr/plex~a-vendre?uc=0",  # Tous les plex au Québec
)

ANALYZER_URL = os.getenv(
    "ANALYZER_URL",
    "https://centris-analyse-bot.onrender.com/analyze",
)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Pause entre deux appels GPT (pour respecter le rate limit)
REQUEST_INTERVAL_SECONDS = int(os.getenv("REQUEST_INTERVAL_SECONDS", "40"))

# Pause entre deux SCANS COMPLETS de la page Centris (ex : 5 min)
FULL_SCAN_INTERVAL_SECONDS = int(os.getenv("FULL_SCAN_INTERVAL_SECONDS", "300"))

SEEN_FILE = "seen_listings.json"


# ===========================
# UTILITAIRES
# ===========================

def load_seen_ids():
    """Charge la liste des IDs déjà analysés."""
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_seen_ids(ids_set):
    """Sauvegarde la liste des IDs déjà analysés."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(ids_set)), f, ensure_ascii=False, indent=2)


def extract_listing_id(url: str):
    """Extrait l'ID Centris (7–8 chiffres) depuis l'URL."""
    m = re.search(r"/(\d{7,8})(?:[^\d]|$)", url)
    return m.group(1) if m else None


def get_listing_urls_from_search():
    """Récupère toutes les URLs de fiches plex sur la page de recherche."""
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

        # On évite les versions anglaises et la vue Map
        if "plexes-for-sale" in href or "view=Map" in href:
            continue

        # On garde seulement les fiches FR de type plex
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
    """Appelle ton API /analyze sur Render pour cette annonce."""
    print(f"🧠 Analyse : {url}")
    try:
        resp = requests.post(
            ANALYZER_URL,
            json={"url": url},
            headers={"Content-Type": "application/json"},
            timeout=120,
        )
    except Exception as e:
        print(f"  ❌ Erreur réseau vers analyseur : {e}")
        return None

    if resp.status_code != 200:
        print(f"  ❌ Erreur HTTP {resp.status_code} : {resp.text[:300]}")
        if "rate limit" in resp.text.lower():
            print("  ⚠️ Rate limit détecté, on stoppe ce cycle proprement.")
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
# CALCULS 100% BASÉS SUR LES VRAIS CHIFFRES
# ===========================

def _as_number_or_zero(v):
    """Retourne le nombre si c'en est un, sinon 0 (on ignore ce champ)."""
    return v if isinstance(v, (int, float)) else 0.0


def enrich_metrics_without_defaults(data: dict) -> dict:
    """
    Calcule NOI, cap rate et cashflow mensuel en utilisant
    UNIQUEMENT les nombres déjà présents dans le JSON.

    - On NE RAJOUTE PAS de montants inventés.
    - Si une dépense est absente ou null -> on la traite comme 0 dans le calcul.
    """

    if not isinstance(data, dict):
        return data

    po = data.get("property_overview") or {}
    rev = data.get("revenus") or {}
    dep = data.get("depenses_vraies") or {}
    hyp = data.get("hypotheses") or {}
    metrics = data.get("metrics") or {}

    prix = po.get("prix")
    revenu_brut = rev.get("revenu_brut_potentiel_annuel")

    # Si pas de prix ou pas de revenu -> pas de calcul possible
    if not isinstance(prix, (int, float)) or not isinstance(revenu_brut, (int, float)) or prix <= 0:
        data["metrics"] = metrics
        return data

    taxes_mun = _as_number_or_zero(dep.get("taxes_municipales"))
    taxes_sco = _as_number_or_zero(dep.get("taxes_scolaires"))
    assurances = _as_number_or_zero(dep.get("assurances"))
    autres = _as_number_or_zero(dep.get("autres_depenses_connues"))

    vacance = hyp.get("vacance_pourcentage")
    vacance = vacance if isinstance(vacance, (int, float)) else 0.0
    entretien = hyp.get("entretien_annuel")
    entretien = entretien if isinstance(entretien, (int, float)) else 0.0

    revenu_net = revenu_brut * (1 - vacance)
    depenses_totales = taxes_mun + taxes_sco + assurances + autres + entretien

    noi = revenu_net - depenses_totales
    cap_rate = (noi / prix) * 100
    cashflow_mensuel = noi / 12

    metrics["noi_estime_annuel"] = round(noi, 2)
    metrics["cap_rate_estime"] = round(cap_rate, 2)
    metrics["cashflow_mensuel_estime"] = round(cashflow_mensuel, 2)

    data["metrics"] = metrics
    return data


def _replace_none_for_display(obj):
    """Pour Discord : remplace None par 'N/A' uniquement pour l'affichage."""
    if isinstance(obj, dict):
        return {k: _replace_none_for_display(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_none_for_display(x) for x in obj]
    if obj is None:
        return "N/A"
    return obj


def send_discord_message(data: dict, url: str):
    """Enrichit avec les metrics puis envoie sur Discord."""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️ Aucun webhook Discord configuré.")
        return

    enriched = enrich_metrics_without_defaults(data)
    display_data = _replace_none_for_display(enriched)

    content = (
        f"**Nouvelle annonce analysée !**\n"
        f"{url}\n"
        f"```json\n{json.dumps(display_data, indent=2, ensure_ascii=False)}\n```"
    )

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
    """Un scan complet : récupère la page, détecte les nouvelles annonces, les analyse, envoie sur Discord."""
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
    """Boucle infinie : Render va lancer ce script et il tournera en continu."""
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
