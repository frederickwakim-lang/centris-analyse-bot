import os
import json
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# Clé API OpenAI depuis .env
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# On limite la taille du texte envoyé au modèle
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "8000"))


def fetch_html(url: str) -> str:
    """
    Télécharge le HTML brut d'une URL Centris avec un vrai User-Agent
    (quand on l'utilise en local).
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def clean_html(html: str) -> str:
    """
    Nettoie le HTML pour ne garder que du texte.
    On enlève scripts, styles, etc., puis on tronque pour ne pas exploser le contexte.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]


def analyze_with_openai(text: str) -> dict:
    """
    Envoie le texte nettoyé de la fiche Centris à OpenAI
    et demande un JSON structuré.
    """

    system = """
Tu es un expert en analyse immobilière au Québec.
Tu DOIS retourner strictement un JSON avec cette structure (clés exactes) :

{
  "property_overview": {
    "type_propriete": string | null,
    "ville": string | null,
    "quartier": string | null,
    "prix": number | null,
    "nb_logements": number | null
  },
  "revenus": {
    "revenu_brut_potentiel_annuel": number | null
  },
  "depenses_vraies": {
    "taxes_municipales": number | null,
    "taxes_scolaires": number | null,
    "assurances": number | null,
    "autres_depenses_connues": number | null
  },
  "hypotheses": {
    "vacance_pourcentage": number | null,
    "entretien_annuel": number | null
  },
  "metrics": {
    "cap_rate_estime": number | null,
    "cashflow_mensuel_estime": number | null,
    "noi_estime_annuel": number | null
  }
}

Règles importantes :
- Utilise des nombres (pas de strings) pour les montants et pourcentages.
- Si une information n'est clairement pas trouvable dans le texte, mets null (et NON 0).
- Si le revenu brut potentiel annuel est clair (total des loyers sur 12 mois), remplis-le.
- Si le prix de vente est clair, remplis-le.
- Si tu peux raisonnablement déduire un champ à partir du texte, fais-le.
"""

    user = f"""
Voici le texte brut d'une annonce Centris (Québec). Analyse-la et remplis le JSON demandé.

Texte :
```text
{text}
```"""

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    raw = completion.choices[0].message.content
    return json.loads(raw)


def _to_num(v):
    """Convertit v en float si possible, sinon None."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def enrich_results(data: dict) -> dict:
    """
    À partir de ce que le modèle a sorti, calcule NOI, cap rate, cashflow.
    Si les infos sont insuffisantes, on laisse les champs à null.
    """

    property_overview = data.get("property_overview") or {}
    revenus = data.get("revenus") or {}
    depenses = data.get("depenses_vraies") or {}
    hypotheses = data.get("hypotheses") or {}
    metrics = data.get("metrics") or {}

    prix = _to_num(property_overview.get("prix"))
    revenu_brut = _to_num(revenus.get("revenu_brut_potentiel_annuel"))

    taxes_mun = _to_num(depenses.get("taxes_municipales")) or 0.0
    taxes_sco = _to_num(depenses.get("taxes_scolaires")) or 0.0
    assurances = _to_num(depenses.get("assurances")) or 0.0
    autres = _to_num(depenses.get("autres_depenses_connues")) or 0.0
    entretien = _to_num(hypotheses.get("entretien_annuel")) or 0.0

    vacance_pct = _to_num(hypotheses.get("vacance_pourcentage"))
    if vacance_pct is None:
        vacance_pct = 0.05  # par défaut 5 %

    if prix is not None and prix > 0 and revenu_brut is not None:
        revenu_net_apres_vacance = revenu_brut * (1 - vacance_pct)
        depenses_totales = taxes_mun + taxes_sco + assurances + autres + entretien
        noi = revenu_net_apres_vacance - depenses_totales

        cap_rate = (noi / prix) * 100.0
        mensualite_hypotheque = (prix * 0.06) / 12.0  # hypothèse simple
        cashflow_mensuel = (noi / 12.0) - mensualite_hypotheque

        metrics["noi_estime_annuel"] = round(noi, 2)
        metrics["cap_rate_estime"] = round(cap_rate, 2)
        metrics["cashflow_mensuel_estime"] = round(cashflow_mensuel, 2)
    else:
        metrics.setdefault("noi_estime_annuel", None)
        metrics.setdefault("cap_rate_estime", None)
        metrics.setdefault("cashflow_mensuel_estime", None)

    data["metrics"] = metrics
    return data


def analyser_centris(input_content: str) -> dict:
    """
    Point d'entrée : prend soit une URL, soit du HTML.
    - Si ça commence par http : on télécharge la page (pour usage local).
    - Sinon : on considère que c'est du HTML brut.
    """

    if input_content.strip().startswith("http"):
        html = fetch_html(input_content.strip())
    else:
        html = input_content

    cleaned = clean_html(html)
    base = analyze_with_openai(cleaned)
    enriched = enrich_results(base)
    return enriched
