import os
import json
import requests
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from openai import OpenAI

# 🔹 Charge les variables d'environnement (.env en local, Render en prod)
load_dotenv()

app = Flask(__name__)

# 🔹 Client OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 🔹 Limite de taille pour le texte envoyé au modèle
MAX_CHARS_FOR_GPT = int(os.getenv("MAX_CHARS_FOR_GPT", "20000"))

# 🔹 Headers pour Centris (pour éviter les 403)
CENTRIS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
}


def fetch_html_from_url(url: str) -> str:
    """Télécharge une page Centris avec un User-Agent réaliste."""
    resp = requests.get(url, headers=CENTRIS_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def clean_html_for_llm(html: str) -> str:
    """Nettoie le HTML Centris et retourne un texte compact pour GPT."""
    soup = BeautifulSoup(html, "html.parser")

    # On enlève scripts, styles, noscript, meta, link
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()

    # Texte visible
    text = soup.get_text(separator="\n", strip=True)

    # On enlève les lignes vides
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text_clean = "\n".join(lines)

    # On coupe pour ne pas dépasser une taille raisonnable
    if len(text_clean) > MAX_CHARS_FOR_GPT:
        text_clean = text_clean[:MAX_CHARS_FOR_GPT]

    return text_clean


def call_openai_structured(text_clean: str) -> dict:
    """
    Envoie le texte nettoyé à OpenAI et récupère un JSON structuré.

    ⚠ IMPORTANT :
    On n'utilise PAS `response_format` car la version de la lib OpenAI
    sur Render ne supporte pas ce paramètre pour Responses.create().
    On donne donc des instructions très strictes au modèle pour
    qu'il renvoie uniquement du JSON, puis on fait `json.loads` dessus.
    """
    system_msg = (
        "Tu es un analyste immobilier spécialisé au Québec. "
        "Tu reçois le texte d'une annonce (déjà nettoyé) et tu dois en extraire "
        "des informations structurées, en JSON."
    )

    user_msg = f"""
Voici le texte d'une annonce immobilière :

\"\"\" 
{text_clean}
\"\"\"


Analyse cette annonce et renvoie UNIQUEMENT un JSON avec la structure suivante
(et rien d'autre, pas de texte autour, pas de commentaires) :

{{
  "property_overview": {{
    "prix": number | null,
    "type_propriete": string | null,
    "ville": string | null,
    "quartier": string | null,
    "nb_logements": number | null
  }},
  "revenus": {{
    "revenu_brut_potentiel_annuel": number | null
  }},
  "depenses_vraies": {{
    "taxes_municipales": number | null,
    "taxes_scolaires": number | null,
    "assurances": number | null,
    "autres_depenses_connues": number | null
  }},
  "hypotheses": {{
    "vacance_pourcentage": number | null,
    "entretien_annuel": number | null
  }},
  "metrics": {{
    "cap_rate_estime": number | null,
    "cashflow_mensuel_estime": number | null
  }}
}}

Rappels IMPORTANTS :
- Si une donnée n'est pas trouvable, mets-la à null.
- Ne renvoie STRICTEMENT RIEN D'AUTRE que ce JSON valide.
- Pas de texte avant ou après, pas de ```json, pas de commentaires.
"""

    # ⚠️ Ici : PAS de response_format
    resp = client.responses.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        input=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        # PAS de `response_format=...` pour rester compatible
    )

    # Récupère le texte de sortie
    # (format Responses : output[0].content[0].text)
    try:
        output_text = resp.output[0].content[0].text
    except Exception as e:
        # Si la structure change, on renvoie tout pour debug
        return {
            "error": f"Structure de réponse OpenAI inattendue: {e}",
            "raw_response": str(resp),
        }

    # On essaie de parser en JSON
    try:
        data = json.loads(output_text)
    except Exception as e:
        # Si jamais le JSON est mal formé, on renvoie brut pour debug
        data = {
            "error": f"Impossible de parser le JSON renvoyé par OpenAI: {e}",
            "raw": output_text,
        }

    return data


def analyze_with_openai(content_or_url: str) -> dict:
    """
    Si content_or_url commence par 'http', on le traite comme une URL Centris.
    Sinon, on considère que c'est du HTML collé.
    """
    if content_or_url.startswith("http"):
        html = fetch_html_from_url(content_or_url)
    else:
        html = content_or_url

    text_clean = clean_html_for_llm(html)
    result = call_openai_structured(text_clean)
    return result


# =========================
#        ROUTE WEB
# =========================
@app.route("/", methods=["GET", "POST"])
def index():
    """
    Interface web :
    - mode "single" : 1 lien ou contenu collé
    - mode "batch"  : plusieurs liens Centris, un par ligne
    """
    error = None
    result = None
    batch_results = None

    if request.method == "POST":
        mode = request.form.get("mode", "single")

        # 🔹 MODE 1 : une annonce
        if mode == "single":
            content = request.form.get("content", "").strip()
            if not content:
                error = "Veuillez entrer un lien Centris ou coller le contenu de la page."
            else:
                try:
                    result = analyze_with_openai(content)
                except Exception as e:
                    error = f"Erreur d'analyse : {e}"

        # 🔹 MODE 2 : plusieurs liens (un par ligne)
        elif mode == "batch":
            raw = request.form.get("urls", "")
            urls = [u.strip() for u in raw.splitlines() if u.strip()]
            if not urls:
                error = "Veuillez entrer au moins un lien (un par ligne)."
            else:
                batch_results = []
                for u in urls:
                    item = {"url": u}
                    try:
                        item_result = analyze_with_openai(u)
                        item["data"] = item_result
                    except Exception as e:
                        item["error"] = f"Erreur d'analyse : {e}"
                    batch_results.append(item)

    return render_template(
        "index.html",
        error=error,
        result=result,
        batch_results=batch_results,
    )


# =========================
#        ROUTE API
# =========================
@app.route("/analyze", methods=["POST"])
def analyze_api():
    """
    Endpoint API utilisé par ton watcher et d'autres scripts.

    Accepte un JSON de deux façons possibles :
    - { "url": "https://www.centris.ca/..." }
    - ou { "content": "<HTML complet de la page>" }

    Dans les deux cas, on passe par OpenAI après nettoyage.
    """
    data = request.get_json(silent=True) or {}

    url = (data.get("url") or "").strip()
    content = (data.get("content") or "").strip()

    if not url and not content:
        return jsonify({"error": "Il faut fournir 'url' ou 'content' dans le JSON."}), 400

    try:
        if url:
            result = analyze_with_openai(url)
        else:
            result = analyze_with_openai(content)
        return jsonify(result), 200
    except requests.HTTPError as e:
        return jsonify({"error": f"Erreur HTTP en téléchargeant la page Centris: {e}"}), 502
    except Exception as e:
        return jsonify({"error": f"Erreur calculs/analyse: {e}"}), 500


if __name__ == "__main__":
    # Debug local
    app.run(host="0.0.0.0", port=5000, debug=True)
