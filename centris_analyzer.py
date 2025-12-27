import re
from bs4 import BeautifulSoup


def _to_number(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)

    txt = str(s)
    txt = txt.replace("\u00a0", " ").strip()

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


def _find_value_near_labels(text: str, labels):
    t = " ".join(text.split())
    for lab in labels:
        m = re.search(rf"{lab}\s*[:\-]?\s*([0-9][0-9\s\u00a0\.,\$]*)", t, flags=re.IGNORECASE)
        if m:
            return _to_number(m.group(1))
    return None


def analyser_centris(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    out = {
        "__analyzer_version__": "v2-2025-12-27",

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

    # Prix
    out["property_overview"]["prix"] = _find_value_near_labels(
        text, ["Prix", "Prix demandé", "Price"]
    )

    # Taxes municipales / scolaires
    out["depenses_vraies"]["taxes_municipales"] = _find_value_near_labels(
        text, ["Taxes municipales", "Taxe municipale", "Municipal taxes"]
    )

    out["depenses_vraies"]["taxes_scolaires"] = _find_value_near_labels(
        text, ["Taxes scolaires", "Taxe scolaire", "School taxes"]
    )

    # Revenu brut annuel
    out["revenus"]["revenu_brut_potentiel_annuel"] = _find_value_near_labels(
        text, ["Revenu brut", "Revenus bruts", "Gross income"]
    )

    # Nb logements
    m = re.search(r"\b(\d+)\s+(logements|logement|unités|unites)\b", text, flags=re.IGNORECASE)
    if m:
        try:
            out["property_overview"]["nb_logements"] = int(m.group(1))
        except Exception:
            pass

    return out
