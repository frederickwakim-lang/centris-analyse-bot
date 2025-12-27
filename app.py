import os
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import requests

from centris_analyzer import analyser_centris

load_dotenv()

app = Flask(__name__)


def fetch_html_from_url(url: str) -> str:
    """Télécharge une page (Centris) avec un vrai User-Agent."""
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


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    batch_results = None
    error = None

    if request.method == "POST":
        mode = request.form.get("mode", "single")
        content = (request.form.get("content") or "").strip()
        urls_text = (request.form.get("urls") or "").strip()

        try:
            # ----- MODE 1 : UNE SEULE ANNONCE -----
            if mode == "single":
                if not content:
                    error = "Veuillez entrer une URL ou du HTML."
                else:
                    if content.startswith("http"):
                        html = fetch_html_from_url(content)
                    else:
                        html = content

                    result = analyser_centris(html)

            # ----- MODE 2 : PLUSIEURS LIENS -----
            elif mode == "batch":
                urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
                if not urls:
                    error = "Veuillez entrer au moins 1 URL."
                else:
                    batch_results = []
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


@app.route("/analyze", methods=["POST"])
def api_analyze():
    """
    Endpoint API pour ton watcher ou des scripts.
    JSON d'entrée :
      - soit {"url": "https://..."}
      - soit {"content": "<html>...</html>"}
    """
    body = request.get_json(silent=True) or {}

    url = body.get("url")
    content = body.get("content")

    if not url and not content:
        return jsonify({"error": "Il faut fournir 'url' ou 'content'."}), 400

    try:
        if url:
            html = fetch_html_from_url(url)
        else:
            html = content

        data = analyser_centris(html)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": f"Erreur calculs/analyse: {e}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    # Render met le port dans la variable d'environnement PORT
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
