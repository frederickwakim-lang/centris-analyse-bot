import os
import json
from bs4 import BeautifulSoup
from openai import OpenAI

# Client OpenAI (clé prise dans .env)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# On limite la taille du texte envoyé au modèle
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "9000"))


# =========================================================
# 1) NETTOYAGE DU HTML
# =========================================================
def _clean_html(html: str) -> str:
    """
    Enlève scripts, styles, etc. et garde seulement du texte brut.
    On tronque à MAX_CHARS_FOR_GPT pour ne pas exploser le contexte.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]


# =========================================================
# 2) APPEL OPENAI : EXTRACTION DES DONNÉES BRUTES
# =========================================================
def _call_openai_extraction(text: str) -> dict:
    """
    Envoie le texte nettoyé à OpenAI et demande un JSON structuré
    avec prix, revenus, taxes, etc. (mais SANS calculer le cap rate).
    """

    system_message = """
Tu es un expert en analyse immobilière au Québec.

Ton rôle : EXTRAIRE les informations chiffrées d'une annonce Centris
(et uniquement ça). NE FAIS PAS DE CALCULS, ils seront faits par le backend.

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
- UTILISE des nombres (pas de strings) pour les montants et pourcentages.
- Si une information n'est pas clairement trouvable : mets null.
- Ne mets JAMAIS "N/A" ou du texte dans un champ numérique.
- NE CALCULE PAS le cap rate, ni le cashflow : laisse-les à null.
"""

    user_message = f"""
Voici le texte brut d'une annonce immobilière (souvent Centris).
Analyse-la et remplis le JSON SELON LES RÈGLES ci-dessus.

Texte :
```text
{text}
```"""

    resp = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        response_format={"type": "json_object"},
    )

    raw = resp.output[0].content[0].text
    return json.loads(raw)


# =========================================================
# 3) CALCULS : NOI, CAP RATE, CASHFLOW
# =========================================================
def _to_num(v):
    """Convertit une valeur en float si possible, sinon None."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace("\u00a0", " ").replace(" ", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _enrich_metrics(data: dict) -> dict:
    """
    Utilise les champs extraits (prix, revenus, taxes, entretien, vacance)
    pour calculer :
      - noi_estime_annuel
      - cap_rate_estime
      - cashflow_mensuel_estime
    Si infos insuffisantes → on laisse les métriques à null.
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
        vacance_pct = 0.05  # 5 % par défaut

    # Si on a au moins prix + revenu brut, on peut calculer
    if prix is not None and prix > 0 and revenu_brut is not None:
        revenu_net_apres_vacance = revenu_brut * (1 - vacance_pct)
        depenses_totales = taxes_mun + taxes_sco + assurances + autres + entretien
        noi = revenu_net_apres_vacance - depenses_totales

        cap_rate = (noi / prix) * 100.0

        # Hypothèse simple : 6 % d'intérêt / an sur le prix, payé sur 12 mois
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


# =========================================================
# 4) FONCTION PUBLIQUE UTILISÉE PAR app.py
# =========================================================
def analyser_centris(html: str) -> dict:
    """
    Point d’entrée appelé par app.py.
    On reçoit du HTML complet (déjà téléchargé côté watcher ou côté serveur),
    on nettoie, on envoie à OpenAI, puis on calcule les métriques.
    """
    cleaned = _clean_html(html)
    base = _call_openai_extraction(cleaned)
    enriched = _enrich_metrics(base)
    return enriched
