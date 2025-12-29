import os
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import requests

from centris_analyzer import analyser_centris

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15 MB


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
    Endpoint API pour watcher/scripts.

    JSON d'entrée accepté:
      - {"url": "https://..."}
      - {"content": "<html>...</html>"}
      - {"html": "<html>...</html>"}   ✅ compat watcher
    """
    body = request.get_json(silent=True) or {}

    url = body.get("url")
    content = body.get("content")
    html_direct = body.get("html")

    html = None
    source = None

    if url:
        source = "url"
        try:
            html = fetch_html_from_url(url)
        except Exception as e:
            return jsonify({
                "error": "fetch_failed",
                "source": "url",
                "message": str(e),
                "url": url,
            }), 502

    elif html_direct:
        source = "html"
        html = html_direct

    elif content:
        source = "content"
        html = content

    else:
        return jsonify({
            "error": "missing_input",
            "message": "Il faut fournir 'url' ou 'content' ou 'html'."
        }), 400

    if not html or len(html) < 2000:
        return jsonify({
            "error": "missing_or_too_short_html",
            "source": source,
            "len": len(html) if html else 0,
        }), 400

    try:
        data = analyser_centris(html)

        if isinstance(data, dict):
            data.setdefault("__analyzer_version__", "UNKNOWN")
            data.setdefault("raw_debug", {})
            data.setdefault("_api_debug", {})
            data["_api_debug"].update({
                "source": source,
                "html_len": len(html),
            })

        return jsonify(data), 200

    except Exception as e:
        return jsonify({
            "error": "analyzer_exception",
            "message": str(e),
            "source": source,
        }), 500


# ✅ ALIAS POUR TAMPERMONKEY
@app.post("/api/analyze_html")
def api_analyze_html():
    return api_analyze()


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
