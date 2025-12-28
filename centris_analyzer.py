import re
import json
from typing import Any, Optional, Dict, Tuple, List, Iterable
from bs4 import BeautifulSoup

ANALYZER_VERSION = "v8-2025-12-28-nextdata-first"


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
# HTML helpers
# -----------------------------
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


def _first_match_money(text: str, patterns) -> Optional[int]:
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            val = _money_to_int(m.group(1))
            if val is not None:
                return val
    return None


def _extract_price_from_visible(lines) -> Optional[int]:
    """
    Fallback ultime: essaie de retrouver un prix dans le texte visible.
    (Moins fiable que le JSON, utilisé seulement si JSON absent.)
    """
    blob_top = "\n".join(lines[:350])

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

    if not candidates:
        for m in re.finditer(r'(\d[\d\s,\.]{2,})\s*\$', blob_top):
            p = _money_to_int(m.group(1))
            if p:
                candidates.append(p)

    candidates = [p for p in candidates if p not in (20_000_000, 26_908_000)]
    candidates = [p for p in candidates if 20_000 <= p <= 15_000_000]

    if not candidates:
        return None

    return max(candidates)


def _extract_revenue_from_visible(lines) -> Optional[int]:
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

    # Heuristique Centris: parfois "$24" => "$24,000"
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
        r"residential\s*\((\d{1,3})\)",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            n = _as_int(m.group(1))
            if n is not None and 0 < n < 500:
                return n
    return None


# -----------------------------
# JSON extraction (Centris / Next.js)
# -----------------------------
def _extract_next_data(html: str) -> Tuple[Optional[dict], Optional[str]]:
    """
    Cherche __NEXT_DATA__ (Next.js) ou un script JSON similaire.
    Retourne (json_obj, error_message).
    """
    if not html:
        return None, "empty_html"

    soup = BeautifulSoup(html, "html.parser")

    # 1) Next.js canonical
    s = soup.find("script", id="__NEXT_DATA__")
    if s and s.string:
        raw = s.string.strip()
        try:
            return json.loads(raw), None
        except Exception as e:
            return None, f"next_data_json_error:{e}"

    # 2) fallback: script type application/json (rare, mais parfois)
    for sc in soup.find_all("script"):
        t = (sc.get("type") or "").lower().strip()
        if t in ("application/json", "application/ld+json"):
            continue
        # certains sites ont un gros JSON sans type; on évite de tout parse aveuglément.
    return None, "next_data_not_found"


def _iter_json(obj: Any, path: Tuple[Any, ...] = ()) -> Iterable[Tuple[Tuple[Any, ...], Any]]:
    """
    Parcours récursif de JSON (dict/list) pour pouvoir chercher des clés n'importe où.
    Yields: (path, value)
    """
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
    """
    Cherche une valeur monétaire (int) dans le JSON, en matchant des clés par fragments.
    """
    best = None
    best_path = None

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

        # On prend la première plausible (tu peux raffiner ensuite)
        best = mv
        best_path = path
        break

    return best, best_path


def _find_int_in_json(next_data: dict, includes: List[str], excludes: List[str] = None,
                      min_v: int = 0, max_v: int = 10**9) -> Tuple[Optional[int], Optional[Tuple[Any, ...]]]:
    best = None
    best_path = None

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

        best = iv
        best_path = path
        break

    return best, best_path


# -----------------------------
# Main
# -----------------------------
def analyser_centris(html: str) -> dict:
    lines = _clean_text_lines(html)

    # 1) JSON sources
    next_data, next_err = _extract_next_data(html)
    has_next = isinstance(next_data, dict)

    # 2) Prix: JSON-LD -> NextData -> Visible
    price_jsonld = _extract_price_jsonld(html)

    price_next = None
    price_next_path = None
    if has_next:
        # clés possibles (varient) — on cherche large mais sans inventer
        # On exclut des clés qui ressemblent à des taxes/fees
        price_next, price_next_path = _find_money_in_json(
            next_data,
            includes=["price"],
            excludes=["tax", "fee", "unit", "maintenance", "school", "municipal"],
            min_v=20_000,
            max_v=15_000_000
        )
        # si "price" pas trouvé, on tente "asking" / "list"
        if price_next is None:
            price_next, price_next_path = _find_money_in_json(
                next_data,
                includes=["list"],
                excludes=["tax", "fee"],
                min_v=20_000,
                max_v=15_000_000
            )
        if price_next is None:
            price_next, price_next_path = _find_money_in_json(
                next_data,
                includes=["ask"],
                excludes=["tax", "fee"],
                min_v=20_000,
                max_v=15_000_000
            )

    price_visible = _extract_price_from_visible(lines)

    prix = None
    price_source = None
    price_path = None

    # Priorité: jsonld, next, visible
    for candidate, src, pth in (
        (price_jsonld, "jsonld", None),
        (price_next, "next_data", price_next_path),
        (price_visible, "visible", None),
    ):
        if candidate and 20_000 <= candidate <= 15_000_000 and candidate not in (20_000_000, 26_908_000):
            prix = candidate
            price_source = src
            price_path = pth
            break

    # 3) Revenus/Taxes/Units: NextData d'abord, sinon visible
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
        # Revenus (gross revenue / income)
        revenu, revenu_path = _find_money_in_json(
            next_data,
            includes=["gross", "rev"],
            excludes=[],
            min_v=0,
            max_v=200_000_000
        )
        if revenu is None:
            revenu, revenu_path = _find_money_in_json(
                next_data,
                includes=["revenue"],
                excludes=["tax"],
                min_v=0,
                max_v=200_000_000
            )
        if revenu is not None:
            # Heuristique "$24" => "$24,000" si on reçoit une valeur trop petite
            if 0 < revenu < 1000:
                revenu = revenu * 1000
            revenu_source = "next_data"

        # Taxes municipales
        taxes_mun, taxes_mun_path = _find_money_in_json(
            next_data,
            includes=["municipal", "tax"],
            excludes=[],
            min_v=0,
            max_v=50_000_000
        )
        if taxes_mun is not None:
            taxes_mun_source = "next_data"

        # Taxes scolaires
        taxes_sco, taxes_sco_path = _find_money_in_json(
            next_data,
            includes=["school", "tax"],
            excludes=[],
            min_v=0,
            max_v=50_000_000
        )
        if taxes_sco is not None:
            taxes_sco_source = "next_data"

        # Units
        units, units_path = _find_int_in_json(
            next_data,
            includes=["unit"],
            excludes=["suite", "community", "maintenance"],
            min_v=1,
            max_v=500
        )
        if units is None:
            units, units_path = _find_int_in_json(
                next_data,
                includes=["logement"],
                excludes=[],
                min_v=1,
                max_v=500
            )
        if units is not None:
            units_source = "next_data"

    # Visible fallbacks (si JSON n'a rien)
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
        "revenus": {
            "revenu_brut_potentiel_annuel": revenu
        },
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


