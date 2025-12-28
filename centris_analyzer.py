import re
import json
from bs4 import BeautifulSoup

ANALYZER_VERSION = "v3-2025-12-27"

def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\u00a0", " ").replace("\u202f", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _to_number(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    txt = _normalize_text(str(s))
    cleaned = re.sub(r"[^0-9,.\-]", "", txt)
    if cleaned in ("", "-", ".", ","):
        return None
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except Exception:
        return None

def _to_int_money(s):
    v = _to_number(s)
    if v is None:
        return None
    try:
        return int(round(v))
    except Exception:
        return None

def _money_near_labels(text: str, labels, window=500, lo=1, hi=100_000_000):
    t = _normalize_text(text)
    money_pat = re.compile(r"(\$?\s*\d{1,3}(?:[ ,]\d{3})*(?:[.,]\d+)?\s*\$?)")

    for lab in labels:
        m = re.search(rf"(?i){re.escape(lab)}(.{{0,{window}}})", t)
        if not m:
            continue
        seg = m.group(1)
        best = None
        for mm in money_pat.finditer(seg):
            val = _to_int_money(mm.group(1))
            if val is None or not (lo <= val <= hi):
                continue
            if best is None or val > best:
                best = val
        if best is not None:
            return float(best)
    return None

def _first_big_money(text: str, lo=50_000, hi=50_000_000):
    t = _normalize_text(text)
    best = None
    for m in re.finditer(r"(\d{1,3}(?:[ ,]\d{3})+)\s*\$", t):
        val = _to_int_money(m.group(1))
        if val is None or not (lo <= val <= hi):
            continue
        if best is None or val > best:
            best = val
    return float(best) if best is not None else None

def _extract_jsonld(soup):
    out = {}
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue

        items = data if isinstance(data, list) else [data]
        for obj in items:
            if not isinstance(obj, dict):
                continue
            offers = obj.get("offers") or {}
            addr = obj.get("address") or {}
            if isinstance(offers, dict) and "price" in offers:
                p = _to_int_money(offers.get("price"))
                if p is not None:
                    out["prix"] = out.get("prix") or float(p)
            if isinstance(addr, dict):
                if addr.get("addressLocality") and not out.get("ville"):
                    out["ville"] = str(addr.get("addressLocality")).strip()
                if addr.get("addressRegion") and not out.get("quartier"):
                    out["quartier"] = str(addr.get("addressRegion")).strip()
            if obj.get("@type") and not out.get("type_propriete"):
                out["type_propriete"] = str(obj.get("@type")).strip()
    return out

def analyser_centris(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    text = _normalize_text(soup.get_text(" ", strip=True))

    out = {
        "__analyzer_version__": ANALYZER_VERSION,
        "depenses_vraies": {
            "assurances": None,
            "autres_depenses_connues": None,
            "taxes_municipales": None,
            "taxes_scolaires": None
        },
        "hypotheses": {
            "entretien_annuel": None,
            "vacance_pourcentage": None
        },
        "metrics": {
            "cap_rate_estime": None,
            "cashflow_mensuel_estime": None,
            "noi_estime_annuel": None
        },
        "property_overview": {
            "nb_logements": None,
            "prix": None,
            "quartier": None,
            "type_propriete": None,
            "ville": None
        },
        "revenus": {
            "revenu_brut_potentiel_annuel": None
        }
    }

    # Prix / taxes / revenus depuis texte visible
    prix = _money_near_labels(text, ["Prix demandé", "Prix", "Prix de vente", "Asking price"], lo=50_000, hi=50_000_000)
    if prix is None:
        prix = _first_big_money(text, lo=50_000, hi=50_000_000)

    taxes_m = _money_near_labels(text, ["Taxes municipales", "Taxe municipale", "Municipal taxes"], lo=1, hi=2_000_000)
    taxes_s = _money_near_labels(text, ["Taxes scolaires", "Taxe scolaire", "School taxes"], lo=1, hi=2_000_000)
    revenu = _money_near_labels(text, ["Revenu brut", "Revenus bruts", "Revenus", "Revenu annuel", "Gross income"], lo=1_000, hi=50_000_000)

    out["property_overview"]["prix"] = prix
    out["depenses_vraies"]["taxes_municipales"] = taxes_m
    out["depenses_vraies"]["taxes_scolaires"] = taxes_s
    out["revenus"]["revenu_brut_potentiel_annuel"] = revenu

    # Fallback JSON-LD (si dispo)
    ld = _extract_jsonld(soup)
    if out["property_overview"]["prix"] is None and ld.get("prix") is not None:
        out["property_overview"]["prix"] = ld["prix"]
    if ld.get("ville") and not out["property_overview"]["ville"]:
        out["property_overview"]["ville"] = ld["ville"]
    if ld.get("quartier") and not out["property_overview"]["quartier"]:
        out["property_overview"]["quartier"] = ld["quartier"]
    if ld.get("type_propriete") and not out["property_overview"]["type_propriete"]:
        out["property_overview"]["type_propriete"] = ld["type_propriete"]

    # Calcul NOI / cap rate si possible (revenu + au moins une taxe)
    if revenu is not None and (taxes_m is not None or taxes_s is not None):
        dep = 0.0
        if taxes_m is not None:
            dep += float(taxes_m)
        if taxes_s is not None:
            dep += float(taxes_s)
        noi = float(revenu) - dep
        out["metrics"]["noi_estime_annuel"] = round(noi, 2)
        if prix is not None and float(prix) > 0:
            out["metrics"]["cap_rate_estime"] = round((noi / float(prix)) * 100.0, 2)

    return out
