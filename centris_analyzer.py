import re
import json
from typing import Any, Optional, Dict, Tuple, List, Iterable
from bs4 import BeautifulSoup

ANALYZER_VERSION = "v8-2025-12-28-nextdata-first+annualfix+priceheader2"


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

    if "," in s2 and "." not in s2:
        if re.match(r"^-?\d+,\d{2}$", s2):
            s2 = s2.replace(",", ".")
            try:
                return int(round(float(s2)))
            except Exception:
                return None
        s2 = s2.replace(",", "")

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


def _clean_text_lines(html: str):
    soup = BeautifulSoup(html or "", "html.parser")
    lines = soup.get_text("\n", strip=True).replace("\u00a0", " ").replace("\u202f", " ").splitlines()
    lines = [ln.strip() for ln in lines if ln.strip()]
    return lines


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


def _extract_price_from_visible(lines) -> Optional[int]:
    """
    ✅ FIX 2 (robuste):
    - Centris peut rendre le prix plus loin que le titre.
    1) Cherche près du titre "à vendre" (fenêtre plus large)
    2) Fallback: scan début de page, MAIS en filtrant les lignes qui parlent d'évaluation/taxes/dépenses
       pour éviter de ramasser des montants parasites.
    """
    # 1) proche du titre
    idx = None
    for i, ln in enumerate(lines[:400]):
        low = ln.lower()
        if "à vendre" in low or "for sale" in low:
            idx = i
            break

    if idx is not None:
        win = "\n".join(lines[idx: idx + 180])
        m = re.search(r"(\d[\d\s\u00a0\u202f,\.]{2,})\s*\$", win)
        if m:
            p = _money_to_int(m.group(1))
            if p and 20_000 <= p <= 15_000_000:
                return p

    # 2) fallback filtré (début de page)
    ignore_markers = ("évaluation", "evaluation", "taxes", "dépenses", "depenses")
    candidates: List[int] = []

    for ln in lines[:350]:
        low = ln.lower()
        if any(k in low for k in ignore_markers):
            continue

        for mm in re.finditer(r"(\d[\d\s\u00a0\u202f,\.]{2,})\s*\$", ln):
            p2 = _money_to_int(mm.group(1))
            if p2 and 20_000 <= p2 <= 15_000_000:
                candidates.append(p2)

    if not candidates:
        return None

    return max(candidates)


def _extract_units_from_visible(lines) -> Optional[int]:
    text = "\n".join(lines)
    patterns = [
        r"nombre\s+de\s+logements?\s*:?[\s\-]*([0-9]{1,3})",
        r"number\s+of\s+units?\s*:?[\s\-]*([0-9]{1,3})",
        r"residential\s*\((\d{1,3})\)",
    ]
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            n = _as_int(m.group(1))
            if n is not None and 0 < n < 500:
                return n
    return None


def _extract_units_from_title(lines) -> Optional[int]:
    for ln in lines[:10]:
        low = ln.lower()
        if "triplex" in low:
            return 3
        if "duplex" in low:
            return 2
        if "quadruplex" in low or "4-plex" in low:
            return 4
        if "5-plex" in low:
            return 5
    return None


# ✅ FIX: taxes from table-like lines (Centris often shows monthly + annual)
def _extract_taxes_from_tables(lines) -> Dict[str, Optional[int]]:
    """
    Centris affiche souvent 2 valeurs: mensuel + annuel.
    Ex: Municipales: 439 $ puis 5 270 $.
    ✅ On prend la plus grande valeur (annuelle) pour chaque taxe.
    """
    mun_candidates: List[int] = []
    sco_candidates: List[int] = []

    scan = lines[:2000]

    for i in range(len(scan) - 1):
        label = scan[i].lower()
        val_line = scan[i + 1]

        v = _money_to_int(val_line)
        if v is None:
            m = re.search(r"(\d[\d\s,\.]{1,})\s*\$", val_line)
            v = _money_to_int(m.group(1)) if m else None

        if v is None:
            continue

        if ("municipales" in label) or ("municipal" in label):
            mun_candidates.append(v)

        if ("scolaires" in label) or ("school" in label):
            sco_candidates.append(v)

    taxes_mun = max(mun_candidates) if mun_candidates else None
    taxes_sco = max(sco_candidates) if sco_candidates else None

    return {"taxes_municipales": taxes_mun, "taxes_scolaires": taxes_sco}


# ✅ FIX: revenue gross potential can be in "Caractéristiques", not only "Détails financiers"
def _extract_revenue_from_tables(lines) -> Optional[int]:
    """
    Revenus bruts potentiels peut être plus bas dans la page (Caractéristiques).
    ✅ On scanne large (début de page) au lieu de commencer à 'Détails financiers'.
    """
    scan = lines[:2000]

    for i in range(len(scan) - 1):
        label = scan[i].lower()
        if ("revenus bruts potentiels" in label
            or "revenu brut potentiel" in label
            or "potential gross revenue" in label
            or "pot. gross rev" in label):
            val_line = scan[i + 1]
            v = _money_to_int(val_line)
            if v is None:
                m = re.search(r"(\d[\d\s,\.]{1,})\s*\$", val_line)
                v = _money_to_int(m.group(1)) if m else None
            if v is not None and 0 < v < 1000:
                v *= 1000
            return v

    return None


def _first_match_money(text: str, patterns) -> Optional[int]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = _money_to_int(m.group(1))
            if val is not None:
                return val
    return None


def _extract_revenue_from_visible(lines) -> Optional[int]:
    # ✅ try table-mode first
    v_table = _extract_revenue_from_tables(lines)
    if v_table is not None:
        return v_table

    text = "\n".join(lines)
    patterns = [
        r"revenu(?:s)?\s+brut(?:s)?\s+potentiel(?:s)?.*?(\d[\d\s,\.]{1,})\s*\$",
        r"revenu\s+brut.*?(\d[\d\s,\.]{1,})\s*\$",
        r"pot\.\s*gross\s*rev\.\s*:\s*\$?\s*(\d[\d\s,\.]{1,})",
        r"potential\s+gross\s+revenue.*?(\d[\d\s,\.]{1,})\s*\$",
    ]
    v = _first_match_money(text, patterns)
    if v is None:
        return None
    if 0 < v < 1000:
        v = v * 1000
    return v


def _extract_taxes_from_visible(lines) -> Dict[str, Optional[int]]:
    # ✅ try table-mode first
    table = _extract_taxes_from_tables(lines)

    text = "\n".join(lines)
    mun_patterns = [
        r"taxes?\s+municipales?.*?(\d[\d\s,\.]{1,})\s*\$",
        r"municipal\s+tax(?:es)?.*?(\d[\d\s,\.]{1,})\s*\$",
    ]
    sco_patterns = [
        r"taxes?\s+scolaires?.*?(\d[\d\s,\.]{1,})\s*\$",
        r"school\s+tax(?:es)?.*?(\d[\d\s,\.]{1,})\s*\$",
    ]

    taxes_mun = table.get("taxes_municipales")
    taxes_sco = table.get("taxes_scolaires")

    if taxes_mun is None:
        taxes_mun = _first_match_money(text, mun_patterns)
    if taxes_sco is None:
        taxes_sco = _first_match_money(text, sco_patterns)

    return {"taxes_municipales": taxes_mun, "taxes_scolaires": taxes_sco}


def _extract_next_data(html: str) -> Tuple[Optional[dict], Optional[str]]:
    if not html:
        return None, "empty_html"
    soup = BeautifulSoup(html, "html.parser")
    s = soup.find("script", id="__NEXT_DATA__")
    if s and s.string:
        raw = s.string.strip()
        try:
            return json.loads(raw), None
        except Exception as e:
            return None, f"next_data_json_error:{e}"
    return None, "next_data_not_found"


def _iter_json(obj: Any, path: Tuple[Any, ...] = ()) -> Iterable[Tuple[Tuple[Any, ...], Any]]:
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_json(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_json(v, path + (i,))


def _key_match(k: str, includes: List[str], excludes: List[str] = None) -> bool:
    kk = str(k).lower()
    if excludes:
        for ex in excludes:
            if ex in kk:
                return False
    return all(inc in kk for inc in includes)


def _find_money_in_json(next_data: dict, includes: List[str], excludes: List[str] = None,
                        min_v: int = 0, max_v: int = 10**12) -> Tuple[Optional[int], Optional[Tuple[Any, ...]]]:
    for path, val in _iter_json(next_data):
        if not path:
            continue
        key = path[-1]
        if not isinstance(key, str):
            continue
        if not _key_match(key, includes=includes, excludes=excludes or []):
            continue
        mv = _money_to_int(val)
        if mv is None:
            continue
        if not (min_v <= mv <= max_v):
            continue
        return mv, path
    return None, None


def _find_int_in_json(next_data: dict, includes: List[str], excludes: List[str] = None,
                      min_v: int = 0, max_v: int = 10**9) -> Tuple[Optional[int], Optional[Tuple[Any, ...]]]:
    for path, val in _iter_json(next_data):
        if not path:
            continue
        key = path[-1]
        if not isinstance(key, str):
            continue
        if not _key_match(key, includes=includes, excludes=excludes or []):
            continue
        iv = _as_int(val)
        if iv is None:
            continue
        if not (min_v <= iv <= max_v):
            continue
        return iv, path
    return None, None


def analyser_centris(html: str) -> dict:
    lines = _clean_text_lines(html)

    next_data, next_err = _extract_next_data(html)
    has_next = isinstance(next_data, dict)

    price_jsonld = _extract_price_jsonld(html)

    price_next = None
    price_next_path = None
    if has_next:
        price_next, price_next_path = _find_money_in_json(
            next_data,
            includes=["price"],
            excludes=["tax", "fee", "unit", "maintenance", "school", "municipal"],
            min_v=20_000,
            max_v=15_000_000
        )

    price_visible = _extract_price_from_visible(lines)

    prix = None
    price_source = None
    price_path = None

    for candidate, src, pth in (
        (price_jsonld, "jsonld", None),
        (price_next, "next_data", price_next_path),
        (price_visible, "visible", None),
    ):
        if candidate and 20_000 <= candidate <= 15_000_000:
            prix = candidate
            price_source = src
            price_path = pth
            break

    revenu = None
    revenu_source = None
    revenu_path = None

    taxes_mun = None
    taxes_mun_source = None
    taxes_mun_path = None

    taxes_sco = None
    taxes_sco_source = None
    taxes_sco_path = None

    units = None
    units_source = None
    units_path = None

    if has_next:
        revenu, revenu_path = _find_money_in_json(next_data, includes=["revenue"], excludes=["tax"], min_v=0, max_v=200_000_000)
        if revenu is not None:
            if 0 < revenu < 1000:
                revenu *= 1000
            revenu_source = "next_data"

        taxes_mun, taxes_mun_path = _find_money_in_json(next_data, includes=["municipal", "tax"], excludes=[], min_v=0, max_v=50_000_000)
        if taxes_mun is not None:
            taxes_mun_source = "next_data"

        taxes_sco, taxes_sco_path = _find_money_in_json(next_data, includes=["school", "tax"], excludes=[], min_v=0, max_v=50_000_000)
        if taxes_sco is not None:
            taxes_sco_source = "next_data"

        units, units_path = _find_int_in_json(next_data, includes=["unit"], excludes=["suite", "community", "maintenance"], min_v=1, max_v=500)
        if units is not None:
            units_source = "next_data"

    if revenu is None:
        revenu = _extract_revenue_from_visible(lines)
        if revenu is not None:
            revenu_source = "visible"

    if taxes_mun is None or taxes_sco is None:
        taxes_vis = _extract_taxes_from_visible(lines)
        if taxes_mun is None:
            taxes_mun = taxes_vis.get("taxes_municipales")
            if taxes_mun is not None:
                taxes_mun_source = "visible"
        if taxes_sco is None:
            taxes_sco = taxes_vis.get("taxes_scolaires")
            if taxes_sco is not None:
                taxes_sco_source = "visible"

    if units is None:
        units = _extract_units_from_title(lines)
        if units is not None:
            units_source = "title"
    if units is None:
        units = _extract_units_from_visible(lines)
        if units is not None:
            units_source = "visible"

    out = {
        "__analyzer_version__": ANALYZER_VERSION,
        "property_overview": {
            "prix": prix,
            "nb_logements": units,
            "ville": None,
            "quartier": None,
            "type_propriete": None,
        },
        "revenus": {"revenu_brut_potentiel_annuel": revenu},
        "depenses_vraies": {
            "taxes_municipales": taxes_mun,
            "taxes_scolaires": taxes_sco,
        },
        "raw_debug": {
            "has_next_data": has_next,
            "next_data_error": next_err,
            "price_jsonld": price_jsonld,
            "price_next": price_next,
            "price_visible": price_visible,
            "price_source": price_source,
            "price_path": price_path,
            "revenu_source": revenu_source,
            "revenu_path": revenu_path,
            "taxes_mun_source": taxes_mun_source,
            "taxes_mun_path": taxes_mun_path,
            "taxes_sco_source": taxes_sco_source,
            "taxes_sco_path": taxes_sco_path,
            "units_source": units_source,
            "units_path": units_path,
            "lines_top_sample": lines[:25],
        }
    }
    return out
