import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from centris_analyzer import analyser_centris

# Charge les variables d'env (.env en local, Render en prod)
load_dotenv()

app = Flask(__name__)


# ---------------------------------------------------------
#  Page d’accueil : formulaire + affichage résultats
# ---------------------------------------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    error = None
    result = None
    batch_results = None

    if request.method == "POST":
        mode = request.form.get("mode", "single")
        content = (request.form.get("content") or "").strip()
        urls_text = (request.form.get("urls") or "").strip()

        try:
            # --- MODE 1 : une seule annonce (URL ou HTML collé) ---
            if mode == "single":
                if not content:
                    error = "Veuillez entrer une URL ou du HTML."
                else:
                    # analyser_centris gère URL OU HTML complet
                    result = analyser_centris(content)

            # --- MODE 2 : plusieurs liens (un par ligne) ---
            elif mode == "batch":
                urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
                if not urls:
                    error = "Veuillez entrer au moins 1 URL."
                else:
                    batch_results = []
                    for u in urls:
                        item = {"url": u}
                        try:
                            data = analyser_centris(u)
                            item["data"] = data
                        except Exception as e:
                            item["error"] = str(e)
                        batch_results.append(item)

        except Exception as e:
            error = f"Erreur d'analyse : {e}"

    return render_template(
        "index.html",
        error=error,
        result=result,
        batch_results=batch_results,
    )


# ---------------------------------------------------------
#  API /analyze — utilisée par ton watcher
# ---------------------------------------------------------
@app.route("/analyze", methods=["POST"])
def api_analyze():
    """
    Endpoint API pour le watcher et les tests (POST JSON).

    Body JSON :
      - soit {"url": "https://..."}
      - soit {"content": "<html>...</html>"}
    """
    try:
        body = request.get_json(silent=True) or {}
        url = (body.get("url") or "").strip()
        content = (body.get("content") or "").strip()

        if not url and not content:
            return jsonify({"error": "Il faut fournir 'url' ou 'content'."}), 400

        input_content = url or content
        data = analyser_centris(input_content)
        return jsonify(data), 200

    except Exception as e:
        return jsonify({"error": f"Erreur calculs/analyse: {e}"}), 500


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


# ---------------------------------------------------------
#  Lancer l’app localement (dev)
# ---------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
