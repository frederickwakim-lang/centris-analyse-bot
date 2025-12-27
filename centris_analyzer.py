import re
import json
from bs4 import BeautifulSoup


def _to_number(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)

    txt = str(s).replace("\u00a0", " ").strip()

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


def _is_reasonable_money(x, lo=10000, hi=50000000):
    if x is None:
        return False
    try:
        v = float(x)
        return lo <= v <= hi
    except Exception:
        return False


def _deep_find_price(obj):
    """
    Parcourt dict/list récursivement et retourne le premier prix raisonnable trouvé
    dans des clés typiques.
    """
    if isinstance(obj, dict):
        # clés probables
        for k in ["price", "Prix", "prix", "amount", "montant"]:
            if k in obj:
                v = _to_number(obj.get(k))
                if _is_reasonable_money(v):
                    return v

        # JSON-LD: offers.price
        offers = obj.get("offers")
        if offers:
            v = _deep_find_price(offers)
            if _is_reasonable_money(v):
                return v

        # parcours récursif
        for _, v in obj.items():
            got = _deep_find_price(v)
            if _is_reasonable_money(got):
                return got

    elif isinstance(obj, list):
        for it in obj:
            got = _deep_find_price(it)
            if _is_reasonable_money(got):
                return got

    return None


def _extract_jsonld(soup):
    """
    Récupère tous les scripts application/ld+json parsables.
    """
    out = []
    for sc in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = (sc.string or sc.get_text() or "").strip()
        if not txt:
            continue
        try:
            out.append(json.loads(txt))
        except Exception:
            # parfois plusieurs objets collés -> on ignore
            continue
    return out


def _extract_big_json_blobs(soup):
    """
    Heuristique: scripts très longs contenant des { } qu'on essaie de parser.
    """
    blobs = []
    for sc in soup.find_all("script"):
        txt = (sc.string or sc.get_text() or "")
        if len(txt) < 2000:
            continue
        if "{" not in txt or "}" not in txt:
            continue

        # tente de trouver un objet JSON dans le script
        # pattern: = {...};
        m = re.search(r"=\s*({.*})\s*;?\s*$", txt, flags=re.DOTALL)
        if m:
            candidate = m.group(1)
            try:
                blobs.append(json.loads(candidate))
                continue
            except Exception:
                pass

        # pattern: premier { ... dernier }
        first = txt.find("{")
        last = txt.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = txt[first:last + 1]
            try:
                blobs.append(json.loads(candidate))
            except Exception:
                pass

    return blobs


def _find_units_from_text(text):
    m = re.search(r"\b(\d+)\s+(logements|logement|unités|unites)\b", text, flags=re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def analyser_centris(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    out = {
        "__analyzer_version__": "v3-2025-12-27",

        "property_overview": {
            "type_propriete": None,
            "ville": None,
            "quartier": None,
            "nb_logements": None,
            "prix": None,
        },
        "revenus": {
            "revenu_brut_potentiel_annuel": None
        },
        "depenses_vraies": {
            "taxes_municipales": None,
            "taxes_scolaires": None,
            "assurances": None,
            "autres_depenses_connues": None
        },
        "hypotheses": {
            "vacance_pourcentage": None,
            "entretien_annuel": None
        },
        "metrics": {
            "noi_estime_annuel": None,
            "cap_rate_estime": None,
            "cashflow_mensuel_estime": None
        }
    }

    # 1) Units (texte)
    out["property_overview"]["nb_logements"] = _find_units_from_text(text)

    # 2) Prix (JSON-LD puis gros blobs)
    price = None
    for obj in _extract_jsonld(soup):
        price = _deep_find_price(obj)
        if _is_reasonable_money(price):
            break

    if not _is_reasonable_money(price):
        for obj in _extract_big_json_blobs(soup):
            price = _deep_find_price(obj)
            if _is_reasonable_money(price):
                break

    out["property_overview"]["prix"] = price

    # 3) Taxes / revenus: on garde fallback texte, mais on filtre raisonnable
    def find_money(labels):
        v = None
        t = " ".join(text.split())
        for lab in labels:
            m = re.search(rf"{lab}\s*[:\-]?\s*([0-9][0-9\s\u00a0\.,\$]*)", t, flags=re.IGNORECASE)
            if m:
                v = _to_number(m.group(1))
                if _is_reasonable_money(v, lo=1, hi=2000000):  # taxes/revenus plus petits
                    return v
        return None

    out["depenses_vraies"]["taxes_municipales"] = find_money(
        ["Taxes municipales", "Taxe municipale", "Municipal taxes"]
    )
    out["depenses_vraies"]["taxes_scolaires"] = find_money(
        ["Taxes scolaires", "Taxe scolaire", "School taxes"]
    )
    out["revenus"]["revenu_brut_potentiel_annuel"] = find_money(
        ["Revenu brut", "Revenus bruts", "Gross income"]
    )

    return out
