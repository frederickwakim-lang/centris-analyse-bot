import re
import json
import time
import requests
from bs4 import BeautifulSoup

# 🔹 URL de la page de recherche Centris (liste de plex à Montréal)
CENTRIS_SEARCH_URL = "https://www.centris.ca/fr/plex~a-vendre~montreal-ile?uc=0"

# 🔹 URL de ton analyseur déjà déployé sur Render
ANALYZER_URL = "https://centris-analyse-bot.onrender.com/analyze"

# 🔹 Fichier local pour mémoriser les annonces déjà analysées
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
    """
    Trouve un ID Centris à 7 ou 8 chiffres dans l'URL de fiche.
    Exemple: .../19122184
    """
    m = re.search(r"/(\d{7,8})(?:[^\d]|$)", url)
    if m:
        return m.group(1)
    return None


def get_listing_urls_from_search():
    print(f"Téléchargement de {CENTRIS_SEARCH_URL}")
    resp = requests.get(
        CENTRIS_SEARCH_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=20,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    urls = []

    # On récupère tous les liens vers des propriétés
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/fr/propriete" in href or "/fr/maison" in href or "/fr/plex" in href:
            if href.startswith("http"):
                full_url = href
            else:
                full_url = "https://www.centris.ca" + href
            urls.append(full_url)

    # On enlève les doublons en gardant l'ordre
    unique = list(dict.fromkeys(urls))
    print(f"{len(unique)} URLs trouvées (avant filtrage par ID).")
    return unique


def analyze_listing(url: str):
    print(f"Analyse de {url}")
    resp = requests.post(
        ANALYZER_URL,
        json={"content": url},
        headers={"Content-Type": "application/json"},
        timeout=60,
    )

    if resp.status_code != 200:
        print(f"  ❌ Erreur HTTP {resp.status_code} : {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception:
        print("  ❌ Réponse non JSON :")
        print(resp.text[:500])
        return None

    print("  ✅ Analyse OK.")
    return data


def main():
    seen = load_seen_ids()
    print(f"{len(seen)} annonces déjà analysées.\n")

    urls = get_listing_urls_from_search()

    for url in urls:
        listing_id = extract_listing_id(url)

        # 🔸 Si pas d'ID (pas de 7-8 chiffres dans l'URL), on ignore
        if not listing_id:
            print(f"🔸 Pas d'ID dans l'URL, on ignore : {url}")
            continue

        # 🔸 Si déjà vue, on saute
        if listing_id in seen:
            print(f"⏩ Annonce {listing_id} déjà vue, on saute.")
            continue

        # 🔹 Analyse de la fiche détaillée
        data = analyze_listing(url)

        # 🔹 Si OK, on ajoute à la liste des vues
        if data:
            seen.add(listing_id)
            save_seen_ids(seen)

        # Petite pause pour ne pas spammer Centris
        time.sleep(2)


if __name__ == "__main__":
    main()
