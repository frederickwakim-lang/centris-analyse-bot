from dotenv import load_dotenv
load_dotenv()

import os
import time
import requests

WATCHER_TAG = "[WATCHER v2025-12-28]"
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Le watcher ne scrape plus Centris.
# Il envoie un message "mode manuel" puis il dort.
SLEEP_SECONDS = int(os.getenv("MANUAL_WATCHER_SLEEP_SECONDS", "3600"))  # 1h


def send_discord(content: str):
    if not DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL manquant. (Pas d'envoi Discord)", flush=True)
        return
    requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)


def main():
    print(f"{WATCHER_TAG} MANUAL MODE starting…", flush=True)

    # Message unique (tu peux le laisser à chaque reboot, c'est correct)
    send_discord(
        f"{WATCHER_TAG}\n"
        "✅ Mode manuel activé (anti-CAPTCHA Centris)\n\n"
        "Ce bot ne scrape PLUS Centris automatiquement.\n"
        "Pour analyser une fiche :\n\n"
        "1) Ouvre la fiche Centris dans Chrome/Edge\n"
        "2) F12 → Console → colle :\n"
        "   copy(document.documentElement.outerHTML)\n"
        "3) Colle le HTML dans un fichier : listing.html\n"
        "4) Dans PowerShell (dans ton projet) lance :\n"
        "   python manual_submit.py --url \"<URL_CENTRIS>\" --file listing.html --discord\n\n"
        "Résultat : l’analyseur reçoit le VRAI HTML (pas de CAPTCHA) et renvoie les vraies infos."
    )

    # Boucle infinie (service reste en vie sur Render)
    while True:
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()
