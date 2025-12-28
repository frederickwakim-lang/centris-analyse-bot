# centris_analyzer.py
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple
from bs4 import BeautifulSoup

ANALYZER_VERSION = "v6-2025-12-28-money-jsonld-price"


# -----------------------------
# Helpers
# -----------------------------
def _money_to_int(x: Any) -> Optional[int]:
    """
    Convertit un montant en int (CAD) en gérant les formats FR/EN:
      - "908 000 $" -> 908000
      - "3 120,00 $" -> 3120   ✅ (au lieu de 312000)
      - "9,844" -> 9844
      - 3120.0 -> 3120
    """
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(round(x))

    s = str(x).replace("\u00a0", " ").replace("\u202f", " ").strip()

    # garde chiffres, espaces, virgule, point, signe -
    s2 = re.sub(r"[^0-9,\.\-\s]", "", s).strip()
    if not s2:
        return None

    # enlève espaces (séparateurs de milliers)
    s2 = re.sub(r"\s+", "", s2)

    # Cas FR: 3120,00 (virgule décimale)
    if "," in s2 and "." not in s2:
        # si exactement 2 chiffres après virgule => décimal
        if re.match(r"^-?\d+,\d{2}$", s2):
            s2 = s2.replace(",", ".")
            try:
                return int(round(float(s2)))
            except Exception:
                return None
        # sinon virgule = séparateur de milliers
        s2 = s2.replace(",", "")

    # Cas EN: 1,234.56 -> enlever virgules
    if "," in s2 and "." in s2:
        s2 = s2.replace(",", "")

    try:
        return int(round(float(s2)))
    except Exception:
        return None


def _as_int(x: Any) -> Optional[int]:
    """
    Gardé pour les champs non-monétaires (unités, étages, superficies).
    """
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return int(x)
    s = str(x).replace("\u00a0", " ").replace("\u202f", " ").strip()
    digits = re.sub(r"[^\d\-]", "", s)
    if not digits or digits == "-":
        return None
    try:
        return int(digits)
    except Exception:
        return None


def _as_str(x: Any) -> Optional[str]:
    if x is None:
        return None
    s = str(x).strip()
    return s if s else None


def _extract_next_data_json(html: str) -> Optional[dict]:
    if not html:
        return None
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL | re.IGNORECASE)
    if not m:
        return None
    raw = (m.group(1) or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _extract_centris_id_from_html(html: str) -> Optional[str]:
    if not html:
        return None

    m = re.search(r'property="og:url"\s+content="[^"]+/(\d{7,8})(?:[^0-9]|$)', html, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r'rel="canonical"\s+href="[^"]+/(\d{7,8})(?:[^0-9]|$)', html, re.IGNORECASE)
    if m:
        return m.group(1)

    m = re.search(r'/(\d{7,8})(?:[^0-9]|$)', html)
    return m.group(1) if m else None


# -----------------------------
# Price extraction (JSON-LD + header)
# -----------------------------
def _extract_price_from_jsonld(html: str) -> Optional[int]:
    """
    Cherche dans <script type="application/ld+json"> ... offers.price
    """
    if not html:
        return None

    scripts = re.findall(
        r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE
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

            if isinstance(offers, dict):
                if offers.get("price") is not None:
                    return _money_to_int(offers.get("price"))

            elif isinstance(offers, list):
                for off in offers:
                    if isinstance(off, dict) and off.get("price") is not None:
                        p = _money_to_int(off.get("price"))
                        if p:
                            return p

    return None


def _extract_display_price_from_header(html: str) -> Optional[int]:
    """
    Extrait le prix affiché en haut.
    Priorité:
      0) JSON-LD offers.price ✅
      1) meta og/twitter/description
      2) fenêtre autour de "à vendre" (en évitant les petits montants)
      3) fallback top N lignes (en évitant les petits montants)
    """
    if not html:
        return None

    # 0) JSON-LD (le plus fiable)
    p_jsonld = _extract_price_from_jsonld(html)
    if p_jsonld and p_jsonld >= 20_000:  # un "prix" sous 20k est quasi jamais un prix d’immeuble
        return p_jsonld

    # 1) metas
    meta_patterns = [
        r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="twitter:description"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
    ]
    for pat in meta_patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            txt = m.group(1)
            m2 = re.search(r'(\d[\d\s\u00a0\u202f,\.]{2,})\s*\$', txt)
            if m2:
                p = _money_to_int(m2.group(1))
                if p and p >= 20_000:
                    return p

    soup = BeautifulSoup(html or "", "html.parser")
    lines = soup.get_text("\n", strip=True).replace("\u00a0", " ").replace("\u202f", " ").splitlines()

    # 2) fenêtre autour du premier "à vendre"
    idx = None
    for i, ln in enumerate(lines[:700]):
        if "à vendre" in ln.lower():
            idx = i
            break
    if idx is not None:
        window = "\n".join(lines[idx: idx + 80])
        # On cherche TOUS les montants $ dans la fenêtre et on prend le plus plausible (le plus grand >=20k)
        candidates = []
        for m in re.finditer(r'(\d[\d\s,\.]{2,})\s*\$', window):
            p = _money_to_int(m.group(1))
            if p and p >= 20_000:
                candidates.append(p)
        if candidates:
            return max(candidates)

    # 3) fallback top 420 lignes
    top = "\n".join(lines[:420])
    candidates = []
    for m in re.finditer(r'(\d[\d\s,\.]{2,})\s*\$', top):
        p = _money_to_int(m.group(1))
        if p and p >= 20_000:
            candidates.append(p)
    return max(candidates) if candidates else None


# -----------------------------
# Listing selection from __NEXT_DATA__
# -----------------------------
LISTING_SIGNAL_KEYS = {
    "taxes", "municipal", "school", "price", "prix",
    "grosspotentialrevenue", "revenusbrutspotentiels",
    "address", "city", "municipality", "borough", "district", "area",
    "units", "numberofunits", "unitcount",
    "building", "lot", "landarea",
    "id", "listingid", "centrisid", "mlsnumber", "propertyid",
}


def _score_listing_candidate(obj: dict) -> int:
    if not isinstance(obj, dict):
        return -10
    keys = {str(k).lower() for k in obj.keys()}
    hits = len(keys.intersection(LISTING_SIGNAL_KEYS))
    score = min(hits, 12)

    if "taxes" in keys:
        score += 6
    if "price" in keys or "prix" in keys:
        score += 6
    if "grosspotentialrevenue" in keys or "revenusbrutspotentiels" in keys:
        score += 6
    if "address" in keys:
        score += 3
    if "units" in keys or "numberofunits" in keys:
        score += 3

    return score


def _dict_contains_centris_id(d: dict, centris_id: str) -> bool:
    if not isinstance(d, dict) or not centris_id:
        return False
    for k, v in d.items():
        lk = str(k).lower()
        if lk in ("id", "listingid", "centrisid", "propertyid", "mlsnumber", "number", "reference"):
            if str(v).strip() == str(centris_id):
                return True
    return False


def _walk_find_listing_by_id(obj: Any, centris_id: str) -> Optional[dict]:
    found = None

    def walk(o: Any):
        nonlocal found
        if found is not None:
            return
        if isinstance(o, dict):
            if _dict_contains_centris_id(o, centris_id):
                found = o
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(obj)
    return found


def _walk_find_best_listing(obj: Any) -> Tuple[Optional[dict], int]:
    best = None
    best_score = -999

    def walk(o: Any):
        nonlocal best, best_score
        if isinstance(o, dict):
            sc = _score_listing_candidate(o)
            if sc > best_score:
                best_score = sc
                best = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for it in o:
                walk(it)

    walk(obj)
    return best, best_score


def _extract_from_listing(listing: dict) -> Dict[str, Any]:
    # prix (monétaire)
    prix = _money_to_int(listing.get("price") if "price" in listing else listing.get("prix"))

    # revenu brut (monétaire)
    revenu = listing.get("grossPotentialRevenue")
    if revenu is None:
        revenu = listing.get("revenusBrutsPotentiels")
    revenu_brut = _money_to_int(revenu)

    # taxes (monétaire)
    taxes = listing.get("taxes") if isinstance(listing.get("taxes"), dict) else None
    taxes_mun = None
    taxes_sco = None
    if taxes:
        taxes_mun = _money_to_int(taxes.get("municipal") or taxes.get("municipales") or taxes.get("municipalTax"))
        taxes_sco = _money_to_int(taxes.get("school") or taxes.get("scolaires") or taxes.get("schoolTax"))

    # nb logements (int normal)
    nb_logements = None
    units_obj = listing.get("units") if isinstance(listing.get("units"), dict) else None
    if units_obj:
        res = _as_int(units_obj.get("residential") or units_obj.get("residentiel"))
        com = _as_int(units_obj.get("commercial"))
        total = _as_int(units_obj.get("total"))
        if total:
            nb_logements = total
        elif res is not None or com is not None:
            nb_logements = (res or 0) + (com or 0)
    if nb_logements is None:
        nb_logements = _as_int(listing.get("numberOfUnits") or listing.get("nbLogements") or listing.get("unitCount"))

    # location
    ville = None
    quartier = None
    location = listing.get("location") if isinstance(listing.get("location"), dict) else None
    if location:
        ville = _as_str(location.get("city") or location.get("municipality"))
        quartier = _as_str(location.get("borough") or location.get("district") or location.get("area"))

    address = listing.get("address") if isinstance(listing.get("address"), dict) else None
    if not ville and address:
        ville = _as_str(address.get("city") or address.get("municipality"))
    if not quartier and address:
        quartier = _as_str(address.get("borough") or address.get("district"))

    # type propriété
    type_propriete = _as_str(listing.get("propertyType") or listing.get("typePropriete") or listing.get("buildingType"))

    # building details (int normal)
    nb_etages = None
    superficie_habitable = None
    superficie_commerciale = None
    building = listing.get("building") if isinstance(listing.get("building"), dict) else None
    if building:
        nb_etages = _as_int(building.get("floors") or building.get("numberOfFloors"))
        superficie_habitable = _as_int(building.get("livingArea") or building.get("habitableArea"))
        superficie_commerciale = _as_int(building.get("commercialArea") or building.get("commercialAvailableArea"))

    superficie_totale = None
    if superficie_habitable is not None or superficie_commerciale is not None:
        superficie_totale = (superficie_habitable or 0) + (superficie_commerciale or 0)

    # lot (int normal)
    superficie_terrain = None
    lot = listing.get("lot") if isinstance(listing.get("lot"), dict) else None
    if lot:
        superficie_terrain = _as_int(lot.get("landArea") or lot.get("area"))

    return {
        "prix": prix,
        "revenu_brut_potentiel_annuel": revenu_brut,
        "taxes_municipales": taxes_mun,
        "taxes_scolaires": taxes_sco,
        "nb_logements": nb_logements,
        "ville": ville,
        "quartier": quartier,
        "type_propriete": type_propriete,
        "nb_etages": nb_etages,
        "superficie_habitable_sqft": superficie_habitable,
        "superficie_commerciale_sqft": superficie_commerciale,
        "superficie_totale_sqft": superficie_totale,
        "superficie_terrain_sqft": superficie_terrain,
    }


def _price_is_phantom(prix: Optional[int], revenu: Optional[int]) -> bool:
    if prix is None or prix <= 0:
        return True
    if prix >= 15_000_000:
        return True
    if prix < 20_000:  # ✅ évite les 1000$ etc.
        return True
    if revenu and revenu > 0:
        if (prix / revenu) > 60:
            return True
    return False


# -----------------------------
# Text fallback (basic)
# -----------------------------
def _fallback_text_extract(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text("\n", strip=True).replace("\u00a0", " ").replace("\u202f", " ")
    text = re.sub(r"\s+", " ", text).strip()

    # prix (monétaire)
    m = re.search(r"(\d[\d\s,\.]{2,})\s*\$", text[:12000], flags=re.IGNORECASE)
    prix = _money_to_int(m.group(1)) if m else None

    # revenu brut potentiel (monétaire)
    m = re.search(r"Revenus?\s+bruts?\s+potentiels?.*?(\d[\d\s,\.]{2,})\s*\$", text, flags=re.IGNORECASE)
    revenu = _money_to_int(m.group(1)) if m else None

    # taxes (monétaire)
    m = re.search(r"Municipales?.*?(\d[\d\s,\.]{1,})\s*\$", text, flags=re.IGNORECASE)
    taxes_mun = _money_to_int(m.group(1)) if m else None
    m = re.search(r"Scolaires?.*?(\d[\d\s,\.]{1,})\s*\$", text, flags=re.IGNORECASE)
    taxes_sco = _money_to_int(m.group(1)) if m else None

    # type (simple)
    type_propriete = None
    head = text[:1600]
    for tp in ("Quadruplex", "Triplex", "Duplex"):
        if tp.lower() in head.lower():
            type_propriete = tp
            break

    return {
        "prix": prix,
        "revenu_brut_potentiel_annuel": revenu,
        "taxes_municipales": taxes_mun,
        "taxes_scolaires": taxes_sco,
        "nb_logements": None,
        "ville": None,
        "quartier": None,
        "type_propriete": type_propriete,
        "nb_etages": None,
        "superficie_habitable_sqft": None,
        "superficie_commerciale_sqft": None,
        "superficie_totale_sqft": None,
        "superficie_terrain_sqft": None,
    }


# -----------------------------
# Main analyzer
# -----------------------------
def analyser_centris(html: str) -> dict:
    out = {
        "__analyzer_version__": ANALYZER_VERSION,
        "property_overview": {
            "type_propriete": None,
            "ville": None,
            "quartier": None,
            "nb_logements": None,
            "nb_etages": None,
            "superficie_habitable_sqft": None,
            "superficie_commerciale_sqft": None,
            "superficie_totale_sqft": None,
            "superficie_terrain_sqft": None,
            "prix": None,
        },
        "revenus": {"revenu_brut_potentiel_annuel": None},
        "depenses_vraies": {
            "taxes_municipales": None,
            "taxes_scolaires": None,
            "assurances": None,
            "services_publics": None,
            "electricite": None,
            "chauffage": None,
            "deneigement": None,
            "autres_depenses_connues": None,
        },
        "raw_debug": {
            "has_next_data": False,
            "centris_id": None,
            "picked_mode": None,
            "best_listing_score": None,
            "display_price": None,
            "price_source": None,
            "phantom_filtered": False,
        },
    }

    centris_id = _extract_centris_id_from_html(html)
    out["raw_debug"]["centris_id"] = centris_id

    # ✅ prix visible (prioritaire)
    display_price = _extract_display_price_from_header(html)
    out["raw_debug"]["display_price"] = display_price

    next_data = _extract_next_data_json(html)
    if next_data:
        out["raw_debug"]["has_next_data"] = True

        listing = _walk_find_listing_by_id(next_data, centris_id) if centris_id else None
        extracted = None

        if listing:
            out["raw_debug"]["picked_mode"] = "by_id"
            extracted = _extract_from_listing(listing)
        else:
            listing, score = _walk_find_best_listing(next_data)
            out["raw_debug"]["picked_mode"] = "best_score"
            out["raw_debug"]["best_listing_score"] = score
            if isinstance(listing, dict) and score >= 8:
                extracted = _extract_from_listing(listing)

        if extracted:
            revenu = extracted["revenu_brut_potentiel_annuel"]

            # ✅ prix final: header si possible, sinon next_data + anti-phantom
            if display_price:
                prix_final = display_price
                out["raw_debug"]["price_source"] = "header"
            else:
                prix_final = extracted["prix"]
                if _price_is_phantom(prix_final, revenu):
                    prix_final = None
                    out["raw_debug"]["phantom_filtered"] = True
                out["raw_debug"]["price_source"] = "next_data"

            out["property_overview"]["prix"] = prix_final
            out["property_overview"]["nb_logements"] = extracted["nb_logements"]
            out["property_overview"]["ville"] = extracted["ville"]
            out["property_overview"]["quartier"] = extracted["quartier"]
            out["property_overview"]["type_propriete"] = extracted["type_propriete"]
            out["property_overview"]["nb_etages"] = extracted["nb_etages"]
            out["property_overview"]["superficie_habitable_sqft"] = extracted["superficie_habitable_sqft"]
            out["property_overview"]["superficie_commerciale_sqft"] = extracted["superficie_commerciale_sqft"]
            out["property_overview"]["superficie_totale_sqft"] = extracted["superficie_totale_sqft"]
            out["property_overview"]["superficie_terrain_sqft"] = extracted["superficie_terrain_sqft"]

            out["revenus"]["revenu_brut_potentiel_annuel"] = extracted["revenu_brut_potentiel_annuel"]
            out["depenses_vraies"]["taxes_municipales"] = extracted["taxes_municipales"]
            out["depenses_vraies"]["taxes_scolaires"] = extracted["taxes_scolaires"]

            return out

    # fallback texte si pas de next_data ou extraction échouée
    fb = _fallback_text_extract(html)
    out["raw_debug"]["picked_mode"] = "fallback_text"

    if display_price:
        out["property_overview"]["prix"] = display_price
        out["raw_debug"]["price_source"] = "header"
    else:
        prix_final = fb["prix"]
        revenu = fb["revenu_brut_potentiel_annuel"]
        if _price_is_phantom(prix_final, revenu):
            prix_final = None
            out["raw_debug"]["phantom_filtered"] = True
        out["property_overview"]["prix"] = prix_final
        out["raw_debug"]["price_source"] = "fallback"

    out["property_overview"]["nb_logements"] = fb["nb_logements"]
    out["property_overview"]["ville"] = fb["ville"]
    out["property_overview"]["quartier"] = fb["quartier"]
    out["property_overview"]["type_propriete"] = fb["type_propriete"]
    out["property_overview"]["nb_etages"] = fb["nb_etages"]
    out["property_overview"]["superficie_habitable_sqft"] = fb["superficie_habitable_sqft"]
    out["property_overview"]["superficie_commerciale_sqft"] = fb["superficie_commerciale_sqft"]
    out["property_overview"]["superficie_totale_sqft"] = fb["superficie_totale_sqft"]
    out["property_overview"]["superficie_terrain_sqft"] = fb["superficie_terrain_sqft"]

    out["revenus"]["revenu_brut_potentiel_annuel"] = fb["revenu_brut_potentiel_annuel"]
    out["depenses_vraies"]["taxes_municipales"] = fb["taxes_municipales"]
    out["depenses_vraies"]["taxes_scolaires"] = fb["taxes_scolaires"]

    return out
