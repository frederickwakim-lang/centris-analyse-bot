import os
import json
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

# On lit la même URL que ton watcher (CENTRIS_SEARCH_URL ou défaut plex Québec)
CENTRIS_SEARCH_URL = os.getenv(
    "CENTRIS_SEARCH_URL",
    "https://www.centris.ca/fr/plex~a-vendre?uc=0",
)

SEEN_FILE = "seen_listings.json"


def extract_listing_id(url: str):
    m = re.search(r"/(\d{7,8})(?:[^\d]|$)", url)
    return m.group(1) if m else None


def get_listing_urls_from_search():
    print(f"🔎 Téléchargement de la recherche : {CENTRIS_SEARCH_URL}")
    resp = requests.get(
        CENTRIS_SEARCH_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=20,
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    urls = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        # On prend tout ce qui ressemble à une fiche ~a-vendre
        if href.startswith("/fr/") and "~a-vendre" in href:
            if href.startswith("http"):
                full_url = href
            else:
                full_url = "https://www.centris.ca" + href
            urls.append(full_url)

    unique = list(dict.fromkeys(urls))
    print(f"➡️ {len(unique)} URLs trouvées")
    return unique


def main():
    urls = get_listing_urls_from_search()
    ids = []

    for url in urls:
        lid = extract_listing_id(url)
        if lid:
            ids.append(lid)

    ids = sorted(set(ids))
    print(f"📂 {len(ids)} IDs trouvés sur la page, marqués comme 'déjà vus'.")

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(ids, f, ensure_ascii=False, indent=2)

    print(f"✅ Fichier {SEEN_FILE} mis à jour.")
    print("✅ À partir de maintenant, le watcher ignorera ces annonces-là.")


if __name__ == "__main__":
    main()
