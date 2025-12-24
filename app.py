import os
import json
from dotenv import load_dotenv
from flask import Flask, request, render_template, jsonify
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# Chargement du .env
load_dotenv()

app = Flask(__name__)

# --- OpenAI ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "8000"))


# ========== 1) UTILITAIRES HTML ==========

def fetch_html_from_url(url: str) -> str:
    """Télécharge le HTML d’une page (Centris ou autre)."""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def clean_html_for_llm(html: str) -> str:
    """Nettoie le HTML pour ne garder que du texte lisible par le modèle."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CHARS_FOR_GPT]


# ========== 2) OPENAI : EXTRACTION STRUCTURÉE ==========

def call_openai_structured(text: str) -> dict:
    """
    Appelle OpenAI pour extraire les infos IMMOBILIÈRES.
    Ici on demande au modèle de REMPLIR les champs de base
    (prix, revenus, taxes, etc.). Les métriques (cap rate, cashflow)
    seront calculées ensuite dans Python.
    """

    system_message = """
Tu es un expert en analyse immobilière au Québec.

Ton rôle : EXTRAIRE les informations chiffrées d'une annonce Centris
(et uniquement ça). NE FAIS PAS DE CALCULS COMPLEXES, ils seront faits
par le backend.

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


# ========== 3) CALCULS : CAP RATE, CASHFLOW, ETC. ==========

def _to_num(v):
    """Convertit v en float si possible, sinon retourne None."""
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
        vacance_pct = 0.05  # 5 % par défaut si absent

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
        # Si on n'a pas le prix ou le revenu brut, on laisse les métriques à null
        metrics.setdefault("noi_estime_annuel", None)
        metrics.setdefault("cap_rate_estime", None)
        metrics.setdefault("cashflow_mensuel_estime", None)

    data["metrics"] = metrics
    return data


def analyze_with_openai(input_content: str) -> dict:
    """
    Point d’entrée unique : prend soit une URL, soit du HTML / texte brut.
    - Si c’est une URL : on télécharge la page, on nettoie, on envoie à OpenAI.
    - Si c’est du texte HTML : on nettoie et on envoie.
    - Ensuite on calcule cap rate, cashflow, etc. (enrich_metrics).
    """

    if input_content.strip().startswith("http"):
        html = fetch_html_from_url(input_content.strip())
    else:
        html = input_content

    cleaned = clean_html_for_llm(html)
    base = call_openai_structured(cleaned)
    enriched = enrich_metrics(base)
    return enriched


# ========== 4) ROUTES FLASK ==========

@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    result = None
    batch_results = None

    if request.method == "POST":
        mode = request.form.get("mode", "single")

        # --- Mode 1 : une seule annonce (URL ou HTML collé) ---
        if mode == "single":
            content = (request.form.get("content") or "").strip()
            if not content:
                error = "Merci de fournir un lien Centris ou du contenu HTML."
            else:
                try:
                    result = analyze_with_openai(content)
                except Exception as e:
                    error = f"Erreur d'analyse : {e}"

        # --- Mode 2 : plusieurs URLs (une par ligne) ---
        elif mode == "batch":
            urls_text = (request.form.get("urls") or "").strip()
            if not urls_text:
                error = "Merci de fournir au moins un lien (un par ligne)."
            else:
                batch_results = []
                for line in urls_text.splitlines():
                    url = line.strip()
                    if not url:
                        continue
                    try:
                        data = analyze_with_openai(url)
                        batch_results.append({
                            "url": url,
                            "data": data,
                            "error": None,
                        })
                    except Exception as e:
                        batch_results.append({
                            "url": url,
                            "data": None,
                            "error": str(e),
                        })

    return render_template(
        "index.html",
        error=error,
        result=result,
        batch_results=batch_results,
    )


@app.route("/analyze", methods=["POST"])
def api_analyze():
    """
    Endpoint API pour le watcher et les tests (POST JSON).
    Body JSON :
      - soit {"url": "https://..."}
      - soit {"content": "<html>...</html>"}
    """
    payload = request.get_json(silent=True) or {}
    url = payload.get("url")
    content = payload.get("content")

    if not url and not content:
        return jsonify({"error": "Il faut fournir 'url' ou 'content' dans le JSON."}), 400

    try:
        input_content = url or content
        result = analyze_with_openai(input_content)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": f"Erreur calculs/analyse: {e}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
