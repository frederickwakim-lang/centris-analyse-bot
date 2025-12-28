import re
import json
from typing import Optional
from bs4 import BeautifulSoup

ANALYZER_VERSION = "v4-2025-12-28-header-price"


def _as_int(x) -> Optional[int]:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return int(x)
    s = str(x).replace("\u00a0", " ").replace("\u202f", " ")
    s = re.sub(r"[^\d]", "", s)
    return int(s) if s else None


def _extract_display_price_from_header(html: str) -> Optional[int]:
    """
    Extrait le prix affiché en haut de la fiche (ex: 908 000 $)
    """
    if not html:
        return None

    # 1) Meta description (souvent: "Duplex à vendre ... 908 000 $")
    meta_patterns = [
        r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="twitter:description"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="description"[^>]+content="([^"]+)"',
    ]

    for pat in meta_patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            txt = m.group(1)
            m2 = re.search(r'(\d[\d\s\u00a0\u202f]{2,})\s*\$', txt)
            if m2:
                return _as_int(m2.group(1))

    # 2) Haut du texte visible
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text("\n", strip=True).replace("\u00a0", " ").replace("\u202f", " ")
    top = "\n".join(text.splitlines()[:80])

    m = re.search(r'(\d[\d\s]{2,})\s*\$', top)
    return _as_int(m.group(1)) if m else None


def analyser_centris(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True)

    prix_header = _extract_display_price_from_header(html)

    out = {
        "__analyzer_version__": ANALYZER_VERSION,
        "property_overview": {
            "prix": prix_header,
            "ville": None,
            "quartier": None,
            "type_propriete": None,
            "nb_logements": None,
        },
        "revenus": {
            "revenu_brut_potentiel_annuel": None
        },
        "depenses_vraies": {
            "taxes_municipales": None,
            "taxes_scolaires": None,
        },
        "raw_debug": {
            "price_source": "header" if prix_header else "missing"
        }
    }

    return out
