from dotenv import load_dotenv
load_dotenv()  # Charge .env en local

import os
import json
from typing import Any, Dict

from bs4 import BeautifulSoup
from openai import OpenAI

# ===========================
#  CONFIG OPENAI
# ===========================

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "8000"))


# ===========================
#  UTILITAIRES
# ===========================

def _clean_html(html: str) -> str:
    """
    Garde seulement le texte lisible pour l'IA.
    On enlève scripts, styles, etc. et on tronque pour ne pas exploser le contexte.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]


def _to_num(v: Any):
    """Convertit v en float si possible, sinon retourne None (pas 0)."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = (
            v.replace(" ", "")
             .replace("\u00a0", "")
             .replace("$", "")
             .replace("€", "")
             .replace(",", ".")
             .strip()
        )
        try:
            return float(s)
        except ValueError:
            return None
    return None


# ===========================
#  APPEL OPENAI : EXTRACTION
# ===========================

def _extract_from_text_with_openai(text: str) -> Dict[str, Any]:
    """
    Envoie le texte nettoyé de la fiche Centris à OpenAI
    et demande un JSON structuré AVEC DE VRAIS CHIFFRES.
    """

    system_message = """
Tu es un expert en analyse immobilière au Québec.

TON JOB : extraire les CHIFFRES RÉELS (prix, revenus, taxes, assurances, etc.)
à partir du texte d'une annonce (souvent Centris).

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

RÈGLES IMPORTANTES :

1) PRIX
- Le prix de vente est souvent écrit comme : "1 059 000 $", "999 000$", "Prix demandé : 849 000 $".
- Tu dois toujours le convertir en nombre sans symbole ni espace, ex. "1 059 000 $" → 1059000.
- Si plusieurs prix sont mentionnés, prends le prix LISTÉ comme prix demandé / prix de vente.

2) REVENUS
- S'il y a un "Revenu brut potentiel" annuel (ou total des loyers par an), utilise-le directement.
  Ex. "Revenu brut potentiel : 46 200 $/an" → 46200.
- S'il y a seulement les loyers MENSUELS par logement (ex. "645$/mois, 820$/mois, 900$/mois"),
  additionne-les, puis multiplie par 12 pour avoir le revenu brut potentiel annuel.
- Tu dois faire ce calcul si l'information est là (ne laisse pas null juste par paresse).

3) TAXES (municipales / scolaires)
- Si tu vois "Taxes municipales (2024) : 3 456 $", mets 3456.
- Si tu vois "Taxes scolaires : 582 $", mets 582.
- S'il y a plusieurs années, prends l'année la plus récente.

4) ASSURANCES et AUTRES DÉPENSES
- Si l'assurance de l'immeuble est mentionnée (ex. "Assurance : 1 200 $/an"),
  remplis "assurances" avec ce montant annuel.
- S'il y a d'autres dépenses connues clairement (ex. "Déneigement : 600$", "Entretien : 800$",
  "Conciergerie : 1 000$"), tu peux soit les additionner et mettre le total dans
  "autres_depenses_connues", soit mettre le poste le plus important.
- Si vraiment aucune info n'est disponible, mets null.

5) VACANCE & ENTRETIEN (hypothèses)
- Si le texte parle clairement d'un taux de vacance prévu (ex. "Vacance estimée 5%"), mets 0.05.
- Si le texte mentionne un budget d'entretien annuel (ex. "Entretien : 1 500$/an"),
  mets ce montant dans "entretien_annuel".
- Sinon, laisse-les à null (ne pas inventer).

6) METRICS
- NE CALCULE PAS le cap rate, le cashflow, ni le NOI.
- Laisse "cap_rate_estime", "cashflow_mensuel_estime" et "noi_estime_annuel" à null.
- Ces calculs seront faits par le backend Python.

7) GÉNÉRAL
- Utilise des nombres (pas de strings) pour tous les montants.
- Ne mets JAMAIS "N/A", "inconnu" ou du texte dans un champ numérique : utilise null à la place.
- Si une information est clairement là dans le texte, tu DOIS la remplir (pas null).
"""

    user_message = f"""
Voici le texte brut d'une annonce immobilière (souvent une fiche Centris).
Analyse-la attentivement et remplis le JSON SELON LES RÈGLES ci-dessus.

Texte :
```text
{text}
```"""

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


# ===========================
#  CALCULS : NOI, CAP RATE, CASHFLOW
# ===========================

def _enrich_metrics(data: Dict[str, Any]) -> Dict[str, Any]:
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
    # Si pas de vacance, on met 5 % par défaut pour les calculs,
    # mais on laisse la valeur originale (null) dans hypotheses.
    vacance_pct_calc = vacance_pct if vacance_pct is not None else 0.05

    if prix is not None and prix > 0 and revenu_brut is not None:
        revenu_net_apres_vacance = revenu_brut * (1 - vacance_pct_calc)
        depenses_totales = taxes_mun + taxes_sco + assurances + autres + entretien
        noi = revenu_net_apres_vacance - depenses_totales

        cap_rate = (noi / prix) * 100.0

        # Hypothèse simple de mensualité (à ajuster si tu veux)
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


# ===========================
#  FONCTION PUBLIQUE UTILISÉE PAR LE WATCHER ET L’API
# ===========================

def analyze_listing(html_or_text: str) -> Dict[str, Any]:
    """
    Point d’entrée UNIQUE utilisé par :
      - centris_watcher.py (background bot)
      - app.py (/analyze, interface web)

    Tu lui passes soit :
      - le HTML brut de la fiche Centris
      - du texte déjà nettoyé

    Il s'occupe de :
      - nettoyer le HTML
      - appeler OpenAI pour extraire les chiffres
      - calculer NOI, cap rate, cashflow
    """

    if "<html" in html_or_text.lower() or "<head" in html_or_text.lower():
        text = _clean_html(html_or_text)
    else:
        text = html_or_text[:MAX_CHARS_FOR_GPT]

    base = _extract_from_text_with_openai(text)
    enriched = _enrich_metrics(base)
    return enriched
