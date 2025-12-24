import os
import json

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# Charger les variables d'environnement (.env en local, Render en prod)
load_dotenv()

app = Flask(__name__)

# --- OpenAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "8000"))


# =====================================================================
# 1) OUTILS HTML
# =====================================================================

def fetch_html_from_url(url: str) -> str:
    """
    Télécharge le HTML d'une page (Centris ou autre) avec un vrai User-Agent
    pour éviter les erreurs 403.
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


def clean_html_for_llm(html: str) -> str:
    """Enlève scripts/styles et garde seulement du texte, tronqué pour le modèle."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]


# =====================================================================
# 2) OPENAI : EXTRACTION DES DONNÉES (SANS CALCULS)
# =====================================================================

def call_openai_structured(text: str) -> dict:
    """
    Demande au modèle d'extraire uniquement les infos chiffrées.
    Les calculs (NOI, cap rate, cashflow) sont faits après en Python.
    """

    system_message = """
Tu es un expert en analyse immobilière au Québec.

Ton rôle : EXTRAIRE les informations chiffrées d'une annonce immobilière
(texte venant souvent de Centris). NE FAIS PAS DE CALCULS COMPLEXES,
ils seront faits par le backend.

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
    "cashflow_mensuel_estime": null
  }
}

Règles IMPORTANTES :
- UTILISE des nombres (pas de strings) pour les montants et pourcentages.
- Si une information n'est pas clairement trouvable : mets null.
- Ne mets JAMAIS "N/A" ou du texte dans un champ qui doit être numérique.
- Ne calcule PAS le cap rate, ni le cashflow : laisse-les à null.
"""

    user_message = f"""
Voici le texte brut d'une annonce immobilière.
Analyse-la et remplis le JSON SELON LES RÈGLES ci-dessus.

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


# =====================================================================
# 3) CALCULS : NOI, CAP RATE, CASHFLOW
# =====================================================================

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


def enrich_metrics(data: dict) -> dict:
    """
    À partir des champs extraits (prix, revenus, taxes, etc.), calcule :
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
        vacance_pct = 0.05  # 5 % par défaut si vraiment absent

    if prix is not None and prix > 0 and revenu_brut is not None:
        revenu_net_apres_vacance = revenu_brut * (1 - vacance_pct)
        depenses_totales = taxes_mun + taxes_sco + assurances + autres + entretien
        noi = revenu_net_apres_vacance - depenses_totales

        cap_rate = (noi / prix) * 100.0

        # Hypothèse simple pour la mensualité : 6 % du prix par an, sur 12 mois.
        mensualite_hypotheque = (prix * 0.06) / 12.0
        cashflow_mensuel = (noi / 12.0) - mensualite_hypotheque

        metrics["noi_estime_annuel"] = round(noi, 2)
        metrics["cap_rate_estime"] = round(cap_rate, 2)
        metrics["cashflow_mensuel_estime"] = round(cashflow_mensuel, 2)
    else:
        # Infos insuffisantes => on laisse à null
        metrics.setdefault("noi_estime_annuel", None)
        metrics.setdefault("cap_rate_estime", None)
        metrics.setdefault("cashflow_mensuel_estime", None)

    data["metrics"] = metrics
    return data


def analyze_with_openai(input_content: str) -> dict:
    """
    Point d'entrée unique pour l'analyse :
      - si input_content est une URL : on télécharge + nettoie + OpenAI + calculs
      - sinon : on considère que c'est du HTML / texte brut.
    """
    if input_content.strip().startswith("http"):
        html = fetch_html_from_url(input_content.strip())
    else:
        html = input_content

    cleaned = clean_html_for_llm(html)
    base = call_openai_structured(cleaned)
    enriched = enrich_metrics(base)
    return enriched


# =====================================================================
# 4) ROUTES FLASK
# =====================================================================

@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    batch_results = None
    error = None

    if request.method == "POST":
        mode = request.form.get("mode")
        content = (request.form.get("content") or "").strip()
        urls_text = (request.form.get("urls") or "").strip()

        try:
            if mode == "single":
                if not content:
                    error = "Veuillez entrer une URL ou du HTML."
                else:
                    result = analyze_with_openai(content)

            elif mode == "batch":
                urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
                batch_results = []

                if not urls:
                    error = "Veuillez entrer au moins 1 URL."
                else:
                    for u in urls:
                        item = {"url": u}
                        try:
                            data = analyze_with_openai(u)
                            item["data"] = data
                        except Exception as e:  # noqa: BLE001
                            item["error"] = str(e)
                        batch_results.append(item)

        except Exception as e:  # noqa: BLE001
            error = f"Erreur d'analyse : {e}"

    return render_template(
        "index.html",
        result=result,
        batch_results=batch_results,
        error=error,
    )


@app.route("/analyze", methods=["POST"])
def api_analyze():
    """
    Endpoint API pour le watcher ou les tests.
    Body JSON :
      - soit {"url": "https://..."}
      - soit {"content": "<html>...</html>"}
    """
    payload = request.get_json(silent=True) or {}
    url = (payload.get("url") or "").strip()
    content = payload.get("content")

    if not url and not content:
        return jsonify({"error": "Il faut fournir 'url' ou 'content' dans le JSON."}), 400

    try:
        input_content = url or content
        result = analyze_with_openai(input_content)
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"Erreur calculs/analyse: {e}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(debug=True, port=5000)
