import os
import json
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv
import requests

from centris_analyzer import analyser_centris

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024  # 15 MB

# ✅ STRIP pour enlever \n / espaces cachés dans Render
FORM_POST_URL = (os.environ.get("FORM_POST_URL", "") or "").strip()
FORM_FIELDS_JSON = os.environ.get("FORM_FIELDS_JSON", "{}")


def push_to_google_form(payload: dict) -> dict:
    if not FORM_POST_URL:
        return {"ok": False, "error": "FORM_POST_URL missing"}

    try:
        mapping = json.loads(FORM_FIELDS_JSON or "{}")
    except Exception as e:
        return {"ok": False, "error": f"FORM_FIELDS_JSON invalid: {e}"}

    if not isinstance(mapping, dict) or not mapping:
        return {"ok": False, "error": "FORM_FIELDS_JSON missing/empty"}

    def get_by_path(d, path: str):
        cur = d
        for part in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    form_data = {}
    for entry_id, path in mapping.items():
        val = get_by_path(payload, path)
        form_data[entry_id] = "" if val is None else str(val)

    # ✅ Champs cachés Google Forms
    form_data["fvv"] = (os.environ.get("FORM_FVV", "1") or "1").strip()
    fbzx = (os.environ.get("FORM_FBZx", "") or "").strip()
    if fbzx:
        form_data["fbzx"] = fbzx
    form_data["pageHistory"] = "0"

    referer = FORM_POST_URL.replace("/formResponse", "/viewform").strip()

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://docs.google.com",
        "Referer": referer,
    }

    try:
        resp = requests.post(
            FORM_POST_URL,
            data=form_data,
            timeout=30,
            headers=headers,
            allow_redirects=False,
        )

        ok = resp.status_code in (200, 302, 303)
        return {
            "ok": ok,
            "status": resp.status_code,
            "location": resp.headers.get("Location"),
            "used_url": FORM_POST_URL,
            "sent_fbzx": bool(fbzx),
            "body": (resp.text or "")[:200],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
            if mode == "single":
                if not content:
                    error = "Veuillez entrer une URL ou du HTML."
                else:
                    if content.startswith("http"):
                        html = fetch_html_from_url(content)
                    else:
                        html = content
                    result = analyser_centris(html)

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
    body = request.get_json(silent=True) or {}

    url = body.get("url")
    content = body.get("content")
    html_direct = body.get("html")

    html = None
    source = None

    if html_direct:
        source = "html"
        html = html_direct
    elif content:
        source = "content"
        html = content
    elif url:
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

            if body.get("push_form") is True:
                data["_form_push"] = push_to_google_form(data)

        return jsonify(data), 200

    except Exception as e:
        return jsonify({
            "error": "analyzer_exception",
            "message": str(e),
            "source": source,
        }), 500


@app.post("/api/analyze_html")
def api_analyze_html():
    return api_analyze()


@app.route("/health", methods=["GET"])
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=False)
