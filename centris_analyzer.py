import re
import json
from typing import Any, Optional, Dict
from bs4 import BeautifulSoup

ANALYZER_VERSION = "v7-2025-12-28-label-based"


# -----------------------------
# Money parsing (FR/EN)
# -----------------------------
def _money_to_int(x: Any) -> Optional[int]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(round(x))

    s = str(x).replace("\u00a0", " ").replace("\u202f", " ").strip()
    s2 = re.sub(r"[^0-9,\.\-\s]", "", s).strip()
    if not s2:
        return None

    s2 = re.sub(r"\s+", "", s2)

    # FR: 3120,00
    if "," in s2 and "." not in s2:
        if re.match(r"^-?\d+,\d{2}$", s2):
            s2 = s2.replace(",", ".")
            try:
                return int(round(float(s2)))
            except Exception:
                return None
        s2 = s2.replace(",", "")

    # EN: 1,234.56
    if "," in s2 and "." in s2:
        s2 = s2.replace(",", "")

    try:
        return int(round(float(s2)))
    except Exception:
        return None


def _as_int(x: Any) -> Optional[int]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    s = re.sub(r"[^\d\-]", "", str(x))
    if not s or s == "-":
        return None
    try:
        return int(s)
    except Exception:
        return None


# -----------------------------
# Extraction helpers
# -----------------------------
def _extract_price_jsonld(html: str) -> Optional[int]:
    if not html:
        return None

    scripts = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        flags=re.DOTALL | re.IGNORECASE
    )

    for raw in scripts:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue

        candidates = obj if isinstance(obj, list) else [obj]
        for it in candidates:
            if not isinstance(it, dict):
                continue
            offers = it.get("offers")
            if isinstance(offers, dict) and offers.get("price") is not None:
                p = _money_to_int(offers.get("price"))
                if p:
                    return p
            if isinstance(offers, list):
                for off in offers:
                    if isinstance(off, dict) and off.get("price") is not None:
                        p = _money_to_int(off.get("price"))
                        if p:
                            return p
    return None


def _first_match_money(text: str, patterns) -> Optional[int]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = _money_to_int(m.group(1))
            if val is not None:
                return val
    return None


def _clean_text_lines(html: str):
    soup = BeautifulSoup(html or "", "html.parser")
    lines = soup.get_text("\n", strip=True).replace("\u00a0", " ").replace("\u202f", " ").splitlines()
    lines = [ln.strip() for ln in lines if ln.strip()]
    return lines


def _extract_price_from_visible(lines) -> Optional[int]:
    """
    On cherche le prix autour de 'à vendre' / 'for sale' et on prend le montant plausible.
    On ignore 20,000,000 explicitement (placeholder).
    """
    blob_top = "\n".join(lines[:350])

    # 1) fenêtre autour de "à vendre" / "for sale"
    idx = None
    for i, ln in enumerate(lines[:700]):
        low = ln.lower()
        if "à vendre" in low or "for sale" in low:
            idx = i
            break

    candidates = []

    if idx is not None:
        window = "\n".join(lines[idx: idx + 120])
        for m in re.finditer(r'(\d[\d\s,\.]{2,})\s*\$', window):
            p = _money_to_int(m.group(1))
            if p:
                candidates.append(p)

    # 2) fallback top blob
    if not candidates:
        for m in re.finditer(r'(\d[\d\s,\.]{2,})\s*\$', blob_top):
            p = _money_to_int(m.group(1))
            if p:
                candidates.append(p)

    # Filtre: supprimer montants non plausibles
    candidates = [p for p in candidates if p not in (20_000_000, 26_908_000)]  # 26,908,000 vu chez toi: bug pareil
    candidates = [p for p in candidates if 20_000 <= p <= 15_000_000]

    if not candidates:
        return None

    # sur Centris, le vrai prix est souvent le plus "mis en avant" => souvent le max plausible dans la fenêtre
    return max(candidates)


def _extract_revenue_from_visible(lines) -> Optional[int]:
    text = "\n".join(lines)

    # FR/EN variants
    patterns = [
        r"revenu(?:s)?\s+brut(?:s)?\s+potentiel(?:s)?.*?(\d[\d\s,\.]{1,})\s*\$",
        r"revenu\s+brut.*?(\d[\d\s,\.]{1,})\s*\$",
        r"pot\.\s*gross\s*rev\.\s*:\s*\$?\s*(\d[\d\s,\.]{1,})",
        r"potential\s+gross\s+revenue.*?(\d[\d\s,\.]{1,})\s*\$",
    ]
    v = _first_match_money(text, patterns)
    if v is None:
        return None

    # Heuristique Centris: parfois ils affichent "$24" pour "$24,000"
    if 0 < v < 1000:
        v = v * 1000

    return v


def _extract_taxes_from_visible(lines) -> Dict[str, Optional[int]]:
    text = "\n".join(lines)

    mun_patterns = [
        r"taxes?\s+municipales?.*?(\d[\d\s,\.]{1,})\s*\$",
        r"municipal\s+tax(?:es)?.*?(\d[\d\s,\.]{1,})\s*\$",
    ]
    sco_patterns = [
        r"taxes?\s+scolaires?.*?(\d[\d\s,\.]{1,})\s*\$",
        r"school\s+tax(?:es)?.*?(\d[\d\s,\.]{1,})\s*\$",
    ]

    taxes_mun = _first_match_money(text, mun_patterns)
    taxes_sco = _first_match_money(text, sco_patterns)

    return {"taxes_municipales": taxes_mun, "taxes_scolaires": taxes_sco}


def _extract_units_from_visible(lines) -> Optional[int]:
    text = "\n".join(lines)

    patterns = [
        r"nombre\s+de\s+logements?\s*:?[\s\-]*([0-9]{1,3})",
        r"number\s+of\s+units?\s*:?[\s\-]*([0-9]{1,3})",
        r"residential\s*\((\d{1,3})\)",  # "Residential (2)"
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            n = _as_int(m.group(1))
            if n is not None and 0 < n < 500:
                return n
    return None


# -----------------------------
# Main
# -----------------------------
def analyser_centris(html: str) -> dict:
    lines = _clean_text_lines(html)

    price_jsonld = _extract_price_jsonld(html)
    price_visible = _extract_price_from_visible(lines)

    # Prix final: JSON-LD si plausible sinon visible
    prix = None
    price_source = None
    for candidate, src in ((price_jsonld, "jsonld"), (price_visible, "visible")):
        if candidate and 20_000 <= candidate <= 15_000_000 and candidate != 20_000_000:
            prix = candidate
            price_source = src
            break

    revenu = _extract_revenue_from_visible(lines)
    taxes = _extract_taxes_from_visible(lines)
    units = _extract_units_from_visible(lines)

    out = {
        "__analyzer_version__": ANALYZER_VERSION,
        "property_overview": {
            "prix": prix,
            "nb_logements": units,
            "ville": None,
            "quartier": None,
            "type_propriete": None,
        },
        "revenus": {
            "revenu_brut_potentiel_annuel": revenu
        },
        "depenses_vraies": {
            "taxes_municipales": taxes.get("taxes_municipales"),
            "taxes_scolaires": taxes.get("taxes_scolaires"),
        },
        "raw_debug": {
            "price_jsonld": price_jsonld,
            "price_visible": price_visible,
            "price_source": price_source,
            "lines_top_sample": lines[:25],
        }
    }

    return out

