from dotenv import load_dotenv
load_dotenv()

import os
import json
import time
import re
import requests
import traceback
from bs4 import BeautifulSoup

from template1_calcs import Template1Inputs, compute_template1, format_discord_template1

CENTRIS_SEARCH_URL = os.getenv("CENTRIS_SEARCH_URL", "https://www.centris.ca/fr/plex~a-vendre?uc=0")
ANALYZER_BASE_URL = os.getenv("ANALYZER_BASE_URL", "https://centris-analyse-bot.onrender.com").rstrip("/")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

REQUEST_INTERVAL_SECONDS = int(os.getenv("REQUEST_INTERVAL_SECONDS", "40"))
FULL_SCAN_INTERVAL_SECONDS = int(os.getenv("FULL_SCAN_INTERVAL_SECONDS", "300"))

SEEN_FILE = "seen_listings.json"
WATCHER_TAG = "[WATCHER v2025-12-28]"

# Timeouts/retries
ANALYZER_TIMEOUT_SECONDS = int(os.getenv("ANALYZER_TIMEOUT_SECONDS", "120"))
ANALYZER_RETRY = int(os.getenv("ANALYZER_RETRY", "2"))
FETCH_TIMEOUT_SECONDS = int(os.getenv("FETCH_TIMEOUT_SECONDS", "30"))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36"
)


def load_seen_ids():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()
    except Exception:
        return set()


def save_seen_ids(ids_set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(ids_set)), f, ensure_ascii=False, indent=2)


def extract_listing_id(url: str):
    m = re.search(r"/(\d{7,8})(?:[^\d]|$)", url)
    return m.group(1) if m else None


def fetch_html_from_url(url: str) -> str:
    headers = {
        "User-Agent": UA,
        "Accept-Language": "fr-CA,fr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
    resp = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT_SECONDS, allow_redirects=True)
    resp.raise_for_status()
    return resp.text


def get_listing_urls_from_search():
    print(f"🔎 Téléchargement : {CENTRIS_SEARCH_URL}", flush=True)
    resp = requests.get(CENTRIS_SEARCH_URL, headers={"User-Agent": UA}, timeout=FETCH_TIMEOUT_SECONDS)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"]

        if "plexes-for-sale" in href or "view=Map" in href:
            continue
        if not href.startswith("/fr/"):
            continue
        if not any(x in href for x in ["/duplex", "/triplex", "/quadruplex", "/plex"]):
            continue

        urls.append("https://www.centris.ca" + href)

    urls = list(dict.fromkeys(urls))
    print(f"➡️ {len(urls)} URLs trouvées", flush=True)
    return urls


def pick(d: dict, *paths, default=None):
    for path in paths:
        cur = d
        ok = True
        for k in path:
            if not isinstance(cur, dict) or k not in cur:
                ok = False
                break
            cur = cur[k]
        if ok and cur not in (None, "", "N/A"):
            return cur
    return default


# =========================
# HTML VALIDATION
# =========================

def looks_like_listing(html: str, listing_id: str) -> bool:
    if not html:
        return False

    hlow = html.lower()
    if "centris" not in hlow:
        return False

    # Next.js data (fréquent sur Centris)
    if 'id="__next_data__"' in hlow or "id='__next_data__'" in hlow:
        return True

    # L'ID dans le HTML (souvent présent dans un JSON interne)
    if listing_id and listing_id in html:
        return True

    # og:url / canonical
    if listing_id:
        if re.search(
            r'property="og:url"[^>]*content="[^"]*centris[^"]*' + re.escape(listing_id) + r'[^"]*"',
            html,
            re.IGNORECASE
        ):
            return True
        if re.search(
            r'rel="canonical"[^>]*href="[^"]*centris[^"]*' + re.escape(listing_id) + r'[^"]*"',
            html,
            re.IGNORECASE
        ):
            return True

    return False


def detect_hard_block_marker(html: str):
    """
    Retourne (is_blocked: bool, matched_marker: str|None)
    IMPORTANT: on veut savoir EXACTEMENT quel marker a trigger.
    """
    if not html:
        return True, "empty_html"

    h = html.lower()

    # MARKERS "durs" (captcha / blocage) — on garde serré, mais on log le match exact
    hard_markers = [
        "captcha",
        "access denied",
        "forbidden",
        "attention required",
        "verify you are human",

        "cloudflare",
        "cf-chl",
        "cf-ray",

        "ddos-guard",
        "incapsula",
        "distil networks",
        "akamai",

        "request blocked",
        "bot detection",
        "unusual traffic",
    ]

    for mk in hard_markers:
        if mk in h:
            return True, mk

    return False, None


# =========================
# Analyzer POST
# =========================

def _post_to_analyzer(payload: dict):
    url = f"{ANALYZER_BASE_URL}/analyze"
    headers = {"Content-Type": "application/json"}

    try:
        print(f"  DEBUG payload keys={list(payload.keys())} has_content={'content' in payload}", flush=True)
        if "content" in payload:
            print(f"  DEBUG html_len={len(payload['content']) if payload['content'] else 0}", flush=True)
    except Exception:
        pass

    for attempt in range(ANALYZER_RETRY + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=ANALYZER_TIMEOUT_SECONDS)

            if resp.status_code == 200:
                try:
                    return resp.status_code, resp.text, resp.json()
                except Exception:
                    return resp.status_code, resp.text, None

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt < ANALYZER_RETRY:
                    time.sleep(1.2 * (attempt + 1))
                    continue

            try:
                j = resp.json()
                body_preview = json.dumps(j, ensure_ascii=False)[:800]
            except Exception:
                body_preview = (resp.text or "")[:800]
            return resp.status_code, body_preview, None

        except requests.exceptions.Timeout as e:
            if attempt < ANALYZER_RETRY:
                time.sleep(1.2 * (attempt + 1))
                continue
            return None, None, {"_error": "analyzer_network_error", "_message": f"timeout: {e}"}
        except Exception as e:
            return None, None, {"_error": "analyzer_network_error", "_message": str(e)}

    return None, None, {"_error": "analyzer_network_error", "_message": "unknown"}


def analyze_listing(url: str):
    print(f"🧠 Analyse : {url}", flush=True)

    listing_id = extract_listing_id(url)

    try:
        html = fetch_html_from_url(url)
    except Exception as e:
        print(f"  ❌ Erreur fetch HTML : {e}", flush=True)
        return {"_error": "fetch_failed", "_message": str(e), "_url": url}

    html_len = len(html) if html else 0

    is_listing = looks_like_listing(html, listing_id) if listing_id else looks_like_listing(html, "")
    hard_blocked, matched_marker = detect_hard_block_marker(html)

    print(f"  📄 HTML length = {html_len}", flush=True)
    print(f"  DEBUG listing_id={listing_id} hard_blocked={hard_blocked} matched_marker={matched_marker} looks_like_listing={is_listing}", flush=True)

    if hard_blocked:
        return {
            "_error": "html_hard_blocked",
            "_message": "HTML semble être un challenge/captcha/blocked (hard markers).",
            "_blocked_marker": matched_marker,
            "_html_len": html_len,
            "_url": url,
            "_listing_id": listing_id,
        }

    if not is_listing:
        return {
            "_error": "html_not_listing_page",
            "_message": "HTML ne ressemble pas à une fiche Centris (pas __NEXT_DATA__, pas og/canonical, pas id).",
            "_html_len": html_len,
            "_url": url,
            "_listing_id": listing_id,
        }

    payload = {"url": url, "content": html}
    status, body_or_text, data = _post_to_analyzer(payload)

    if isinstance(data, dict) and data.get("_error"):
        data["_html_len"] = html_len
        data["_url"] = url
        data["_listing_id"] = listing_id
        return data

    if status != 200 or not isinstance(data, dict):
        return {
            "_error": "analyzer_http_error",
            "_status": status,
            "_body": body_or_text,
            "_message": None,
            "_html_len": html_len,
            "_url": url,
            "_listing_id": listing_id,
        }

    return data


def build_template1_inputs(data: dict) -> Template1Inputs:
    price = pick(data, ("property_overview", "prix"))
    units = pick(data, ("property_overview", "nb_logements"))
    revenu_brut_annuel = pick(data, ("revenus", "revenu_brut_potentiel_annuel"))
    taxes_scolaires = pick(data, ("depenses_vraies", "taxes_scolaires"))
    taxes_municipales = pick(data, ("depenses_vraies", "taxes_municipales"))

    return Template1Inputs(
        price=price,
        units=units,
        revenu_brut_annuel=revenu_brut_annuel,
        taxes_scolaires=taxes_scolaires,
        taxes_municipales=taxes_municipales,
        assurances=None,
        services_publics=None,
        electricite=None,
        chauffage=None,
        deneigement=None,
        conciergerie=None,
    )


def send_discord_message(data: dict, url: str):
    if not DISCORD_WEBHOOK_URL:
        return

    if isinstance(data, dict) and data.get("_error"):
        content = (
            f"{WATCHER_TAG}\n"
            f"⚠️ Analyse impossible / bloquée\n"
            f"• reason: {data.get('_error')}\n"
            f"• marker: {data.get('_blocked_marker')}\n"
            f"• status: {data.get('_status')}\n"
            f"• body: {data.get('_body')}\n"
            f"• message: {data.get('_message')}\n"
            f"• html_len: {data.get('_html_len')}\n"
            f"• listing_id: {data.get('_listing_id')}\n"
            f"{url}"
        )
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
        return

    if isinstance(data, dict) and data.get("error"):
        content = (
            f"{WATCHER_TAG}\n"
            f"⚠️ API /analyze error\n"
            f"• error: {data.get('error')}\n"
            f"• message: {data.get('message')}\n"
            f"{url}"
        )
        requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)
        return

    inp = build_template1_inputs(data)
    out = compute_template1(inp)
    content = format_discord_template1(url, inp, out)

    content = f"{WATCHER_TAG}\n" + content
    requests.post(DISCORD_WEBHOOK_URL, json={"content": content}, timeout=30)


def run_one_full_scan():
    seen = load_seen_ids()
    print(f"📂 {len(seen)} annonces déjà analysées.", flush=True)

    urls = get_listing_urls_from_search()
    for url in urls:
        listing_id = extract_listing_id(url)
        if not listing_id or listing_id in seen:
            continue

        data = analyze_listing(url)

        if isinstance(data, dict):
            send_discord_message(data, url)
            seen.add(listing_id)
            save_seen_ids(seen)

        time.sleep(REQUEST_INTERVAL_SECONDS)


def main_loop():
    while True:
        run_one_full_scan()
        time.sleep(FULL_SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    print(f"{WATCHER_TAG} starting…", flush=True)

    while True:
        try:
            main_loop()
        except KeyboardInterrupt:
            print("[WATCHER] stopped by user", flush=True)
            raise
        except Exception:
            print("[WATCHER] ERROR — restart in 60s", flush=True)
            traceback.print_exc()
            time.sleep(60)


