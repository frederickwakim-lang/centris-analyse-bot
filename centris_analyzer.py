import os
import json
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# --- OpenAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "8000"))

# --- Headers pour éviter les 403 sur Centris ---
CENTRIS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
}


def fetch_html(url: str) -> str:
    """Télécharge le HTML d’une page Centris avec des vrais headers."""
    resp = requests.get(url, headers=CENTRIS_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def clean_html(html: str) -> str:
    """
    Nettoie le HTML pour ne garder que du texte lisible pour le modèle.
    On coupe à MAX_CHARS_FOR_GPT pour éviter les erreurs de contexte.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]


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


def call_openai_structured(text: str) -> dict:
    """
    Appelle OpenAI pour EXTRAIRE les infos immobilières de base :
    prix, revenus, taxes, assurances, etc.
    Les métriques sont calculées ensuite dans Python.
    """

    system_message = """
Tu es un expert en analyse immobilière au Québec.

Ton rôle : EXTRAIRE les informations chiffrées d'une annonce (souvent Centris)
à partir du texte brut (prix, loyers, taxes, assurances, etc.).

Tu dois retourner STRICTEMENT un JSON avec cette structure :

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
    "cap_rate_estime": null,
    "cashflow_mensuel_estime": null,
    "noi_estime_annuel": null
  }
}

Règles IMPORTANTES :
- UTILISE des nombres (pas de strings) pour les montants et pourcentages.
- Si une information est clairement trouvable ou déductible (par ex : loyers x 12),
  remplis-la.
- Ne mets JAMAIS "N/A" ou du texte dans un champ numérique. Utilise null si vraiment
  impossible même en lisant attentivement.
- Ne calcule PAS le cap rate, ni le cashflow, ni le NOI : laisse-les à null.
"""

    user_message = f"""
Voici le texte brut d'une annonce immobilière (souvent Centris).
Analyse-la et remplis le JSON SELON LES RÈGLES ci-dessus.

Texte :
```text
{text}
```"""

    # 👉 ICI : on utilise chat.completions, PAS responses.create
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
    )

    raw = completion.choices[0].message.content
    return json.loads(raw)


def enrich_metrics(data: dict) -> dict:
    """
    À partir des champs extraits (prix, revenus, taxes, etc.),
    calcule :
      - NOI estimé
      - cap_rate_estime (%)
      - cashflow_mensuel_estime

    Si les infos sont insuffisantes, on laisse les métriques à null.
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
        vacance_pct = 0.05  # 5 % par défaut si rien n'est donné

    if prix is not None and prix > 0 and revenu_brut is not None:
        # Revenu net après vacance
        revenu_net_apres_vacance = revenu_brut * (1 - vacance_pct)
        depenses_totales = taxes_mun + taxes_sco + assurances + autres + entretien
        noi = revenu_net_apres_vacance - depenses_totales

        cap_rate = (noi / prix) * 100.0

        # Hypothèse simple de mensualité d'hypothèque : 6 % du prix par an
        mensualite_hypotheque = (prix * 0.06) / 12.0
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
    Fonction unique utilisée par :
    - le site web (Render)
    - l'API /analyze
    - ton watcher (via l'API)
    """
    if input_content.strip().startswith("http"):
        html = fetch_html(input_content.strip())
    else:
        html = input_content

    cleaned = clean_html(html)
    base = call_openai_structured(cleaned)
    enriched = enrich_metrics(base)
    return enriched
