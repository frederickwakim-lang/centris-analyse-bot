import os
import json
from flask import Flask, render_template, request
import requests
from bs4 import BeautifulSoup
from centris_analyzer_clean import analyser_centris  # ton analyseur GPT

app = Flask(__name__)


# ---------------------------------------------------------
#  FIX 403 : Télécharger une page Centris avec un vrai User-Agent
# ---------------------------------------------------------
def fetch_html_from_url(url: str) -> str:
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


# ---------------------------------------------------------
#  Page d’accueil : formulaire + affichage résultats
# ---------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    batch_results = None
    error = None

    if request.method == "POST":
        mode = request.form.get("mode")
        content = request.form.get("content", "").strip()
        urls_text = request.form.get("urls", "").strip()

        try:
            # ---------------------------------------------------------
            # MODE 1 : UNE SEULE ANNONCE
            # ---------------------------------------------------------
            if mode == "single":
                if not content:
                    error = "Veuillez entrer une URL ou du HTML."
                else:
                    if content.startswith("http"):
                        html = fetch_html_from_url(content)
                    else:
                        html = content

                    result = analyser_centris(html)

            # ---------------------------------------------------------
            # MODE 2 : MULTIPLES LIENS
            # ---------------------------------------------------------
            elif mode == "batch":
                urls = [u.strip() for u in urls_text.split("\n") if u.strip()]
                batch_results = []

                if not urls:
                    error = "Veuillez entrer au moins 1 URL."
                else:
                    for u in urls:
                        item = {"url": u}
                        try:
                            html = fetch_html_from_url(u)
                            data = analyser_centris(html)
                            item["data"] = data
                        except Exception as e:
                            item["error"] = str(e)
                        batch_results.append(item)

        except Exception as e:
            error = f"Erreur d'analyse : {e}"

    return render_template(
        "index.html",
        result=result,
        batch_results=batch_results,
        error=error,
    )


# ---------------------------------------------------------
#  API /analyze — utilisée par ton watcher
# ---------------------------------------------------------
@app.route("/analyze", methods=["POST"])
def api_analyze():
    try:
        body = request.get_json()

        if not body:
            return {"error": "JSON invalide."}, 400

        url = body.get("url")
        content = body.get("content")

        if url:
            html = fetch_html_from_url(url)
        elif content:
            html = content
        else:
            return {"error": "Il faut fournir 'url' ou 'content'."}, 400

        data = analyser_centris(html)
        return data, 200

    except Exception as e:
        return {"error": str(e)}, 500


# ---------------------------------------------------------
#  Lancer l’app localement
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True, port=5000)
