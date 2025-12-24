import re
import json
import time
import os
import requests
from bs4 import BeautifulSoup

# 🔹 URL de la recherche Centris
CENTRIS_SEARCH_URL = os.getenv(
    "CENTRIS_SEARCH_URL",
    "https://www.centris.ca/fr/plex~a-vendre?uc=5",
)

# 🔹 URL de ton analyseur sur Render
ANALYZER_URL = os.getenv(
    "ANALYZER_URL",
    "https://centris-analyse-bot.onrender.com/analyze",
)

# 🔹 Fichier pour stocker les IDs déjà analysés
SEEN_FILE = "seen_listings.json"


def load_seen_ids():
    """Charge la liste des IDs déjà analysés depuis le fichier JSON local."""
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()


def save_seen_ids(ids_set):
    """Sauvegarde la liste des IDs déjà analysés dans le fichier JSON local."""
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(ids_set)), f, ensure_ascii=False, indent=2)


def extract_listing_id(url: str):
    """Extrait l'ID Centris (7–8 chiffres) d'une URL."""
    m = re.search(r"/(\d{7,8})(?:[^\d]|$)", url)
    return m.group(1) if m else None


def get_listing_urls_from_search():
    """Télécharge la page de recherche et récupère toutes les URLs de fiches."""
    print(f"🔎 Téléchargement : {CENTRIS_SEARCH_URL}")
    resp = requests.get(
        CENTRIS_SEARCH_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    urls = []

    # On récupère toutes les URLs de fiches
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(x in href for x in ["/fr/duplex", "/fr/triplex", "/fr/quadruplex", "/fr/plex"]):
            if href.startswith("http"):
                full_url = href
            else:
                full_url = "https://www.centris.ca" + href
            urls.append(full_url)

    unique = list(dict.fromkeys(urls))
    print(f"➡️ {len(unique)} URLs trouvées")
    return unique


def analyze_listing(url: str):
    """Envoie l'URL d'une fiche à ton analyseur Render et retourne le JSON analysé."""
    print(f"🧠 Analyse : {url}")
    try:
        resp = requests.post(
            ANALYZER_URL,
            json={"url": url},
            headers={"Content-Type": "application/json"},
            timeout=45,  # ⏱ max 45 secondes pour éviter de rester bloqué trop longtemps
        )
    except requests.Timeout:
        print("  ❌ Timeout vers l'analyseur (trop long), on saute cette annonce.")
        return None
    except Exception as e:
        print(f"  ❌ Erreur réseau : {e}")
        return None

    if resp.status_code != 200:
        print(f"  ❌ Erreur HTTP {resp.status_code} : {resp.text[:300]}")
        return None

    try:
        data = resp.json()
        print("  ✅ Analyse OK")
        return data
    except Exception:
        print("  ❌ Réponse non JSON :", resp.text[:500])
        return None


def main():
    seen = load_seen_ids()
    print(f"📂 {len(seen)} annonces déjà analysées.\n")

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

        if data:
            seen.add(listing_id)
            save_seen_ids(seen)

        # Petite pause pour ne pas spammer Centris ni ton analyseur
        time.sleep(2)


if __name__ == "__main__":
    main()
