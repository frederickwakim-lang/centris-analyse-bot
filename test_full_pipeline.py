import os
import requests
from dotenv import load_dotenv

load_dotenv()

ANALYZER_URL = os.getenv("ANALYZER_URL", "https://centris-analyse-bot.onrender.com/analyze")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

CENTRIS_URL = "https://www.centris.ca/fr/quadruplex~a-vendre~quebec-la-cite-limoilou/22469257"


def call_analyzer(url: str):
    print(f"🔎 Appel analyseur pour : {url}")
    resp = requests.post(
        ANALYZER_URL,
        json={"url": url},
        headers={"Content-Type": "application/json"},
        timeout=60,
    )
    print("Status analyseur :", resp.status_code)
    print("Texte brut :", resp.text[:400], "...\n")

    resp.raise_for_status()
    return resp.json()


def send_to_discord(data: dict, url: str):
    if not DISCORD_WEBHOOK_URL:
        print("❌ DISCORD_WEBHOOK_URL manquant dans .env")
        return

    overview = data.get("property_overview", {}) or {}
    metrics = data.get("metrics", {}) or {}
    revenus = data.get("revenus", {}) or {}
    depenses = data.get("depenses_vraies", {}) or {}

    titre = f"{overview.get('type_propriete', 'Propriété')} à {overview.get('ville', '')} ({overview.get('quartier', '')})"
    prix = overview.get("prix")
    nb_logements = overview.get("nb_logements")

    cap = metrics.get("cap_rate_estime")
    cashflow = metrics.get("cashflow_mensuel_estime")
    revenu_brut = revenus.get("revenu_brut_potentiel_annuel")

    lignes = []
    lignes.append(f"🧱 **Nouvelle analyse Centris**")
    lignes.append(f"🔗 {url}")
    lignes.append("")
    lignes.append(f"🏷️ {titre}")
    if prix is not None:
        lignes.append(f"💰 Prix demandé : **{prix:,.0f} $**".replace(",", " "))
    if nb_logements:
        lignes.append(f"🏠 Nombre de logements : **{nb_logements}**")
    if revenu_brut is not None:
        lignes.append(f"💵 Revenu brut potentiel annuel : **{revenu_brut:,.0f} $**".replace(",", " "))

    lignes.append("")
    lignes.append("📊 **Analyse financière (si dispo)**")
    if cap is not None:
        lignes.append(f"📈 Cap rate estimé : **{cap:.2f} %**")
    else:
        lignes.append("📈 Cap rate estimé : *non calculé*")

    if cashflow is not None:
        lignes.append(f"💸 Cashflow mensuel estimé : **{cashflow:,.0f} $/mois**".replace(",", " "))
    else:
        lignes.append("💸 Cashflow mensuel estimé : *non calculé*")

    # Petit résumé brut JSON en bas (optionnel)
    lignes.append("")
    lignes.append("```json")
    import json
    lignes.append(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
    lignes.append("```")

    content = "\n".join(lignes)

    print("📨 Envoi sur Discord...")
    resp = requests.post(
        DISCORD_WEBHOOK_URL,
        json={"content": content},
        timeout=30,
    )
    print("Status Discord :", resp.status_code)
    print("Réponse Discord :", resp.text)


def main():
    data = call_analyzer(CENTRIS_URL)
    send_to_discord(data, CENTRIS_URL)


if __name__ == "__main__":
    main()
