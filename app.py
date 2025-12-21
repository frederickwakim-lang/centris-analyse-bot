import os
import json
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, request, Response
from dotenv import load_dotenv
from openai import OpenAI

from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup

# Charger la clé API OpenAI (.env)
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# --- Config Centris + email ---
CENTRIS_SEARCH_URL = os.getenv("CENTRIS_SEARCH_URL")
NOTIFY_EMAIL_FROM = os.getenv("NOTIFY_EMAIL_FROM")
NOTIFY_EMAIL_TO = os.getenv("NOTIFY_EMAIL_TO")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

PROCESSED_IDS_FILE = "processed_listings.json"

app = Flask(__name__)
# On ne s'y fie plus pour l'ordre, mais on le laisse False
app.config["JSON_SORT_KEYS"] = False

# 1) PROMPT : l'IA NE FAIT QUE L'EXTRACTION
EXTRACTION_PROMPT = """
Tu es un analyste immobilier au Québec.
Tu reçois le CONTENU BRUT (texte ou HTML) d’une fiche détaillée (Centris, Remax, PMML, etc.).

Ta mission : extraire UNIQUEMENT les données brutes suivantes, en renvoyant un objet JSON
avec exactement cette structure (pas de texte autour) :

{
  "price": 0,
  "units": 0,
  "revenu_brut_annuel": 0,
  "location": null,
  "floors": null,
  "sqft_total": null,

  "taxes_scolaires": null,
  "taxes_municipales": null,
  "assurances": null,
  "services_publics": null,
  "electricite": null,
  "chauffage": null,
  "deneigement": null,
  "conciergerie": null,

  "notes": ""
}

Règles :

- price : prix demandé de la propriété (pas valeur municipale), en nombre (ex: 600000).
- units : nombre d’unités locatives (plex) en entier.

- revenu_brut_annuel :
  - si revenu brut annuel total est indiqué → tu l’utilises.
  - sinon, tu construis le revenu annuel à partir des loyers mensuels (somme loyers × 12).
  - sinon, TU DOIS L’ESTIMER avec un montant réaliste :
    - par exemple loyer du marché × nb d’unités × 12,
    - ou environ 6–8 % du prix de vente en revenus bruts annuels.
  - ne renvoie JAMAIS 0 sauf si la propriété est clairement sans revenus ET impossible à estimer.

- location : ville/quartier (ex: "Gatineau").
- floors : nb d’étages (ou null si non clair).
- sqft_total : superficie habitable totale en pieds carrés (ou null si non clair).

Dépenses :
- Si la fiche donne un montant annuel : tu le mets (nombre).
- Sinon, laisse à null (NE PAS inventer de chiffres ici).
- Électricité/Chauffage : tu mets un montant uniquement si la fiche indique que le PROPRIÉTAIRE paie.
  Si c’est payé par les locataires, laisse null.

notes :
- court texte avec le nom du site source (Remax, PMML, Centris, etc.) et des remarques utiles.
"""

# ========== UTIL: PERSISTANCE DES IDS DÉJÀ TRAITÉS ==========

def load_processed_ids():
    if not os.path.exists(PROCESSED_IDS_FILE):
        return set()
    try:
        with open(PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_processed_ids(ids_set):
    try:
        with open(PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(ids_set), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Erreur sauvegarde processed_listings:", e)


# 2) FONCTION UTILITAIRE : calculs Python = mêmes idées que ton Excel
def compute_template(extracted: dict) -> dict:
    # Récupération des valeurs brutes
    price = extracted.get("price") or 0
    units = extracted.get("units") or 0

    # Revenu brut annuel : on prend ce que l'IA donne, sinon on ESTIME ici
    revenu = extracted.get("revenu_brut_annuel")
    if not revenu or revenu <= 0:
        if price > 0 and units > 0:
            # Hypothèse : un plex normal donne ~7 % brut du prix par année
            revenu = round(price * 0.07, 2)
        else:
            revenu = 0

    location = extracted.get("location")
    floors = extracted.get("floors")
    sqft_total = extracted.get("sqft_total")

    taxes_scolaires = extracted.get("taxes_scolaires")
    taxes_municipales = extracted.get("taxes_municipales")
    assurances = extracted.get("assurances")
    services_publics = extracted.get("services_publics")
    electricite = extracted.get("electricite")
    chauffage = extracted.get("chauffage")
    deneigement = extracted.get("deneigement")
    conciergerie = extracted.get("conciergerie")

    notes = extracted.get("notes") or ""

    # ---------------- PROPERTY OVERVIEW ----------------
    price_per_unit = price / units if price and units else None
    price_per_sqft = price / sqft_total if price and sqft_total else None
    sqft_per_floor = sqft_total / floors if sqft_total and floors else None

    # ---------------- ESTIMATIONS PYTHON SI MANQUANT ----------------
    # Taxes : si null → approx 2% du prix, 80% muni / 20% scolaire
    if taxes_scolaires is None or taxes_municipales is None:
        if price > 0:
            total_taxes = price * 0.02
            taxes_municipales = taxes_municipales or round(total_taxes * 0.8, 2)
            taxes_scolaires = taxes_scolaires or round(total_taxes * 0.2, 2)
        else:
            taxes_municipales = taxes_municipales or 0
            taxes_scolaires = taxes_scolaires or 0

    # Assurances : si null → max(400 * units, 0.2% du prix), plafonné à 3% du prix
    if assurances is None:
        if price > 0 and units > 0:
            assurance_min = 400 * units
            assurance_brut = price * 0.002
            assurance_max = price * 0.03
            assurances = min(max(assurance_brut, assurance_min), assurance_max)
        else:
            assurances = 0

    # Services publics : si null → estimation simple en fonction du nb d’unités
    if services_publics is None:
        if units <= 2:
            services_publics = 600
        elif units <= 6:
            services_publics = 1000
        else:
            services_publics = 1500

    # Électricité/Chauffage : si null → supposé payé par locataires → 0
    electricite = electricite or 0
    chauffage = chauffage or 0

    # Déneigement : si null → grille
    if deneigement is None:
        if units <= 2:
            deneigement = 800
        elif units <= 6:
            deneigement = 1200
        elif units <= 12:
            deneigement = 1800
        else:
            deneigement = 2500

    # Conciergerie : si null → 400 $ par unité
    if conciergerie is None:
        conciergerie = 400 * units if units > 0 else 0

    # ---------------- DÉPENSES VRAIES ----------------
    total_dep_vraies = (
        (taxes_scolaires or 0)
        + (taxes_municipales or 0)
        + (assurances or 0)
        + (services_publics or 0)
        + (electricite or 0)
        + (chauffage or 0)
        + (deneigement or 0)
        + (conciergerie or 0)
    )

    noi_avant = revenu - total_dep_vraies

    # ---------------- DÉPENSES FAUSSES ----------------
    vacances = revenu * 0.03  # 3%
    entretien = 610 * units
    salaires = 365 * units
    total_dep_fausses = vacances + entretien + salaires

    # ---------------- DÉPENSES TOTALES & FINALE ----------------
    depenses_totales = total_dep_vraies + total_dep_fausses
    rnn = revenu - depenses_totales

    noi_percent = (rnn / revenu) if revenu > 0 else 0
    cap_rate = (rnn / price) if price > 0 else 0

    # ---------------- FINANCEMENT ----------------
    prix_achat = price
    qf = 80  # 80%
    loan = prix_achat * qf / 100 if prix_achat > 0 else 0

    taux = 5.0    # %
    amort = 25    # années par défaut
    r = taux / 100 / 12
    n = amort * 12

    if loan > 0 and r > 0:
        pmt = loan * r * (1 + r) ** n / ((1 + r) ** n - 1)
        service_dette_annuel = pmt * 12
    else:
        pmt = 0
        service_dette_annuel = 0

    dscr = (rnn / service_dette_annuel) if service_dette_annuel > 0 else 0
    noi_fin = rnn
    noi_required = service_dette_annuel * 1.1  # DSCR cible 1.1

    # ---------------- CONSTRUCTION DU JSON FINAL ----------------
    # ⚠️ L’ORDRE ICI est celui qui sortira côté client
    result = {
        "property_overview": {
            "price": price,
            "units": units,
            "revenu_brut_annuel": round(revenu, 2),
            "location": location,
            "floors": floors,
            "sqft_per_floor": sqft_per_floor,
            "sqft_total": sqft_total,
            "price_per_unit": round(price_per_unit, 2) if price_per_unit else None,
            "price_per_sqft": round(price_per_sqft, 2) if price_per_sqft else None,
        },
        "depenses_vraies": {
            "taxes_scolaires": round(taxes_scolaires or 0, 2),
            "taxes_municipales": round(taxes_municipales or 0, 2),
            "assurances": round(assurances or 0, 2),
            "services_publics": round(services_publics or 0, 2),
            "electricite": round(electricite or 0, 2),
            "chauffage": round(chauffage or 0, 2),
            "deneigement": round(deneigement or 0, 2),
            "conciergerie": round(conciergerie or 0, 2),
            "total": round(total_dep_vraies, 2),
            "noi_avant_normalisation": round(noi_avant, 2),
        },
        "depenses_fausses": {
            "vacances": round(vacances, 2),
            "entretien": round(entretien, 2),
            "salaires": round(salaires, 2),
            "total": round(total_dep_fausses, 2)
        },
        "depenses_totales": round(depenses_totales, 2),
        "finale": {
            "rnn": round(rnn, 2),
            "noi_percent": round(noi_percent, 4),  # ex 0.0839 = 8.39%
            "cap_rate": round(cap_rate, 4),
        },
        "financement": {
            "prix_achat": prix_achat,
            "qf": qf,
            "loan": round(loan, 2),
            "taux": taux,
            "amort": amort,
            "pmt": round(pmt, 2),
            "dscr": round(dscr, 3),
            "noi": round(noi_fin, 2),
            "noi_required": round(noi_required, 2),
        },
        "notes": notes,
    }

    return result


# ========== UTIL: APPEL IA COMPLET SUR UN CONTENU BRUT (HTML OU TEXTE) ==========

def analyze_page_content(page_content: str) -> dict:
    """
    Utilise ton prompt d'extraction + compute_template.
    Réutilisé par /analyze ET par le bot automatique Centris.
    """
    messages = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {
            "role": "user",
            "content": (
                "Voici le contenu brut de la fiche détaillée (texte ou HTML) :\n\n"
                f"{page_content}\n\n"
                "Renvoie UNIQUEMENT l'objet JSON d'extraction demandé."
            ),
        },
    ]

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        response_format={"type": "json_object"},
    )
    extraction_json = resp.choices[0].message.content
    extracted = json.loads(extraction_json)

    result_dict = compute_template(extracted)
    return result_dict


# ========== UTIL: ENVOI D'EMAIL ==========

def send_email(subject: str, body: str):
    if not (NOTIFY_EMAIL_FROM and NOTIFY_EMAIL_TO and SMTP_USER and SMTP_PASS):
        print("Email non configuré correctement dans .env, skip send_email.")
        return

    msg = MIMEMultipart()
    msg["From"] = NOTIFY_EMAIL_FROM
    msg["To"] = NOTIFY_EMAIL_TO
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        print("Email envoyé à", NOTIFY_EMAIL_TO)
    except Exception as e:
        print("Erreur envoi email:", e)


# ========== UTIL: SCRAPER CENTRIS ==========

def get_listing_links_from_search_page():
    """
    Va sur la page de recherche Centris et récupère les liens individuels des annonces.
    """
    if not CENTRIS_SEARCH_URL:
        print("CENTRIS_SEARCH_URL non défini, skip scraping.")
        return []

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; CentrisBot/1.0)"
        }
        r = requests.get(CENTRIS_SEARCH_URL, headers=headers, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "NoMLS=" in href or "/fr/propriete" in href:
                if href.startswith("http"):
                    url = href
                else:
                    url = "https://www.centris.ca" + href
                if url not in links:
                    links.append(url)

        return links

    except Exception as e:
        print("Erreur récupération page de recherche Centris:", e)
        return []


def download_listing_html(url: str) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; CentrisBot/1.0)"
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print("Erreur download listing:", url, e)
        return ""


def extract_listing_id_from_url(url: str) -> str:
    """
    Essaie d'extraire un ID unique de l'URL (ex: NoMLS=12345678).
    C'est juste pour déterminer si on l'a déjà traité.
    """
    if "NoMLS=" in url:
        part = url.split("NoMLS=")[-1]
        part = part.split("&")[0]
        return part
    # fallback : l'URL complète comme ID
    return url


# ========== TÂCHE AUTOMATIQUE : NOUVELLES ANNONCES CENTRIS ==========

def process_new_centris_listings():
    print("=== Tâche automatique: scan Centris ===")
    processed_ids = load_processed_ids()

    links = get_listing_links_from_search_page()
    print(f"Liens trouvés sur la page de recherche: {len(links)}")

    new_count = 0

    for url in links:
        listing_id = extract_listing_id_from_url(url)

        # Si déjà traité, on saute
        if listing_id in processed_ids:
            continue

        print(f"Nouvelle annonce détectée: {url}")

        html = download_listing_html(url)
        if not html:
            print("HTML vide, skip.")
            continue

        try:
            data = analyze_page_content(html)
        except Exception as e:
            print("Erreur analyse OpenAI sur", url, "->", e)
            continue

        # Construire un corps d'email lisible
        body = "Nouvelle annonce Centris détectée:\n\n"
        body += f"URL: {url}\n\n"
        body += "Données analysées:\n"
        body += json.dumps(data, ensure_ascii=False, indent=2)

        send_email(subject="Nouvelle annonce Centris analysée", body=body)

        # Marquer comme traité
        processed_ids.add(listing_id)
        new_count += 1

    save_processed_ids(processed_ids)
    print(f"Scan terminé. Nouvelles annonces traitées: {new_count}")


# ========== FLASK ROUTES EXISTANTES + ROUTE DE TEST ==========

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    if not data or "content" not in data:
        return Response(
            json.dumps({"error": "Champ 'content' manquant"}, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status=400,
        )

    raw_content = (data["content"] or "").strip()

    # 1) Si c’est une URL, on va chercher la page (ex: lien Centris)
    if raw_content.startswith("http://") or raw_content.startswith("https://"):
        url = raw_content
        try:
            resp_page = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                },
                timeout=15,
            )
            resp_page.raise_for_status()
            page_content = resp_page.text
        except Exception as e:
            err = {"error": f"Erreur en récupérant l'URL: {url} -> {e}"}
            return Response(
                json.dumps(err, ensure_ascii=False, indent=2),
                mimetype="application/json",
                status=500,
            )
    else:
        # 2) Sinon, on considère que tu as collé le HTML/texte brut
        page_content = raw_content

    try:
        result_dict = analyze_page_content(page_content)
        json_text = json.dumps(result_dict, ensure_ascii=False, indent=2)
        return Response(json_text, mimetype="application/json")
    except Exception as e:
        err = {"error": f"Erreur calculs/analyse: {e}"}
        return Response(
            json.dumps(err, ensure_ascii=False, indent=2),
            mimetype="application/json",
            status=500,
        )


@app.route("/", methods=["GET"])
def index():
    return """
    <!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8" />
        <title>Analyse immobilière (template Excel)</title>
      </head>
      <body>
        <h1>Analyse d'annonce immobilière</h1>

        <p>Colle ici le contenu de la fiche (texte ou HTML) <b>ou simplement le lien Centris</b> :</p>
        <textarea id="content" style="width:100%;height:300px;"></textarea>

        <br><br>
        <button onclick="send()">Analyser</button>

        <pre id="result"></pre>

        <script>
          async function send() {
            const content = document.getElementById('content').value;
            const result = document.getElementById('result');
            result.textContent = "Analyse en cours...";

            try {
              const resp = await fetch('/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
              });

              const text = await resp.text();
              result.textContent = text;
            } catch (e) {
              result.textContent = "Erreur: " + e;
            }
          }
        </script>
      </body>
    </html>
    """


@app.route("/scan-une-fois", methods=["GET"])
def scan_une_fois():
    """
    Route pratique pour déclencher un scan manuel (test).
    """
    process_new_centris_listings()
    return "Scan Centris effectué."


# ========== SCHEDULER DE FOND ==========

scheduler = BackgroundScheduler()
scheduler.add_job(process_new_centris_listings, "interval", minutes=15)
scheduler.start()


if __name__ == "__main__":
    # Port dynamique pour Render (PORT) + 5000 en local
    port = int(os.environ.get("PORT", 5000))
    # debug=False pour éviter que le reloader lance le scheduler deux fois
    app.run(host="0.0.0.0", port=port, debug=False)
