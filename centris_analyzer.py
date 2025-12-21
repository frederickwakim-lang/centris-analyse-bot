import requests
from bs4 import BeautifulSoup

def analyser_centris(url: str) -> dict:
    """
    Extrait des infos d'une page Centris.
    (Pour l'instant c'est du fake juste pour tester Render.)
    Tu vas remplacer ça plus tard par ton vrai code.
    """

    # Télécharger la page
    response = requests.get(url)
    response.raise_for_status()
    html = response.text

    # Parser le HTML
    soup = BeautifulSoup(html, "html.parser")

    # ---------
    # Ces valeurs sont FAKE pour l'instant.
    # Le but est que le site fonctionne sur Render.
    prix = 200000
    taxes_municipales = 1800
    taxes_scolaires = 300
    revenu_brut_potentiel = 30000
    noi = 22000
    cap_rate = 6.5
    cashflow = 400
    # ---------

    return {
        "prix": prix,
        "taxes_municipales": taxes_municipales,
        "taxes_scolaires": taxes_scolaires,
        "revenu_brut_potentiel": revenu_brut_potentiel,
        "noi": noi,
        "cap_rate": cap_rate,
        "cashflow": cashflow,
    }
