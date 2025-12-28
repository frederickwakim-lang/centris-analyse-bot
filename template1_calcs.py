from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any


# Constantes Template 1 (selon ton sheet)
QF_DEFAULT = 0.80          # QF 80%
RATE_DEFAULT = 0.04        # ✅ Taux 4.00%
AMORT_YEARS_DEFAULT = 40   # ✅ Amort 40
DSCR_TARGET_DEFAULT = 1.10 # DSCR 1,1

VACANCY_RATE_DEFAULT = 0.03   # Vacances 3%
SALARIES_RATE_DEFAULT = 0.05  # Salaires 5%
MAINTENANCE_FIXED_DEFAULT = 610.0  # Entretien 610
CONCIERGE_FIXED_DEFAULT = 365.0    # Conciergerie 365


def fnum(x) -> Optional[float]:
    if x is None:
        return None
    try:
        if isinstance(x, str):
            s = (
                x.replace("$", "")
                 .replace(" ", "")
                 .replace("\u00a0", "")
                 .replace(",", "")
            )
            if s == "" or s.lower() in ("n/a", "na"):
                return None
            return float(s)
        return float(x)
    except Exception:
        return None


def nz(x: Optional[float], default: float = 0.0) -> float:
    return default if x is None else float(x)


def safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    a = fnum(a); b = fnum(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def pmt_monthly(principal: float, annual_rate: float, years: int, payments_per_year: int = 12) -> float:
    r = annual_rate / payments_per_year
    n = years * payments_per_year
    if n <= 0:
        return 0.0
    if r == 0:
        return principal / n
    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def money(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    return f"${x:,.0f}"


def pct(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    return f"{x * 100:.2f}%"


@dataclass
class Template1Inputs:
    # PROPERTY OVERVIEW
    price: Optional[float] = None
    units: Optional[int] = None
    revenu_brut_annuel: Optional[float] = None
    taxes_scolaires: Optional[float] = None
    taxes_municipales: Optional[float] = None

    # Dépenses (vraies)
    assurances: Optional[float] = None
    services_publics: Optional[float] = None
    electricite: Optional[float] = None
    chauffage: Optional[float] = None
    deneigement: Optional[float] = None
    conciergerie: Optional[float] = None  # si None -> default 365

    # Dépenses (fausses) / normalisation
    vacances_rate: float = VACANCY_RATE_DEFAULT
    entretien_fixed: float = MAINTENANCE_FIXED_DEFAULT
    salaires_rate: float = SALARIES_RATE_DEFAULT

    # Financement
    qf: float = QF_DEFAULT
    taux: float = RATE_DEFAULT
    amort_years: int = AMORT_YEARS_DEFAULT
    payments_per_year: int = 12
    dscr_target: float = DSCR_TARGET_DEFAULT

    # Offre max / valeur
    cap_target_offer: float = 0.05  # Offre maximale 5%


def compute_template1(inp: Template1Inputs) -> Dict[str, Any]:
    price = fnum(inp.price)
    gross = fnum(inp.revenu_brut_annuel)

    # Dépenses vraies
    concierge = fnum(inp.conciergerie)
    if concierge is None:
        concierge = CONCIERGE_FIXED_DEFAULT

    depenses_vraies = (
        nz(inp.taxes_scolaires)
        + nz(inp.taxes_municipales)
        + nz(inp.assurances)
        + nz(inp.services_publics)
        + nz(inp.electricite)
        + nz(inp.chauffage)
        + nz(inp.deneigement)
        + nz(concierge)
    )

    noi_avant_norm = None
    if gross is not None:
        noi_avant_norm = gross - depenses_vraies

    # Dépenses "fausses" (normalisation)
    vacances = (gross * inp.vacances_rate) if gross is not None else None
    salaires = (gross * inp.salaires_rate) if gross is not None else None
    entretien = inp.entretien_fixed

    depenses_fausses = nz(vacances) + nz(salaires) + nz(entretien)
    depenses_totales = depenses_vraies + depenses_fausses

    noi = None
    if gross is not None:
        noi = gross - depenses_totales

    cap_rate = safe_div(noi, price)

    # Financement (QF)
    loan = None
    down = None
    if price is not None:
        loan = price * inp.qf
        down = price - loan

    pmt = None
    ds_annual = None
    if loan is not None:
        pmt = pmt_monthly(loan, inp.taux, inp.amort_years, inp.payments_per_year)
        ds_annual = pmt * inp.payments_per_year

    dscr = safe_div(noi, ds_annual)
    noi_required = None
    if ds_annual is not None:
        noi_required = ds_annual * inp.dscr_target

    cashflow = None
    if noi is not None and ds_annual is not None:
        cashflow = noi - ds_annual

    # Offre maximale à cap_target_offer (ex: 5%)
    offre_max = None
    if noi is not None and inp.cap_target_offer and inp.cap_target_offer > 0:
        offre_max = noi / inp.cap_target_offer

    valeur = offre_max

    refinance_cash = None
    if valeur is not None and price is not None:
        refinance_cash = (valeur * inp.qf) - (price * inp.qf)

    noi_pct = safe_div(noi, gross)

    out = {
        "price": price,
        "units": inp.units,
        "revenu_brut_annuel": gross,

        "depenses_vraies": depenses_vraies,
        "depenses_fausses": depenses_fausses,
        "depenses_totales": depenses_totales,

        "noi_avant_norm": noi_avant_norm,
        "noi": noi,

        "cap_rate": cap_rate,
        "noi_pct": noi_pct,

        "qf": inp.qf,
        "loan": loan,
        "down_payment": down,
        "taux": inp.taux,
        "amort_years": inp.amort_years,
        "pmt_monthly": pmt,
        "ds_annual": ds_annual,
        "dscr": dscr,
        "dscr_target": inp.dscr_target,
        "noi_required": noi_required,

        "cashflow": cashflow,

        "cap_target_offer": inp.cap_target_offer,
        "offre_max": offre_max,
        "valeur": valeur,
        "refinance_cash": refinance_cash,
    }
    return out


def format_discord_template1(url: str, inp: Template1Inputs, out: Dict[str, Any]) -> str:
    lines = []
    lines.append("[CALCS v2025-12-28]")  # ✅ TAG
    lines.append("**🏢 Nouvelle annonce (Template 1)**")
    lines.append(url)

    lines.append("\n**PROPERTY OVERVIEW**")
    lines.append(f"• Price: {money(out.get('price'))}")
    lines.append(f"• Units: {out.get('units') if out.get('units') is not None else 'N/A'}")
    lines.append(f"• Revenu brut ($/ans): {money(out.get('revenu_brut_annuel'))}")
    lines.append(f"• Taxes Scolaires: {money(inp.taxes_scolaires)}")
    lines.append(f"• Taxes Municipales: {money(inp.taxes_municipales)}")

    lines.append("\n**Dépenses (vraies)**")
    lines.append(f"• Total: {money(out.get('depenses_vraies'))}")

    lines.append("\n**NOI**")
    lines.append(f"• NOI (Avant normalisation): {money(out.get('noi_avant_norm'))}")
    lines.append(f"• NOI (normalisé): {money(out.get('noi'))}")
    lines.append(f"• NOI%: {pct(out.get('noi_pct'))}")
    lines.append(f"• CAP rate: {pct(out.get('cap_rate'))}")

    lines.append("\n**Financement**")
    lines.append(f"• QF: {pct(out.get('qf'))}")
    lines.append(f"• Loan: {money(out.get('loan'))}")
    lines.append(f"• Down: {money(out.get('down_payment'))}")
    lines.append(f"• Taux: {pct(out.get('taux'))}")  # ✅ affichera 4.00%
    lines.append(f"• Amort: {out.get('amort_years') if out.get('amort_years') is not None else 'N/A'}")  # ✅ 40
    lines.append(f"• PMT: {money(out.get('pmt_monthly'))}")
    lines.append(f"• DSCR: {out.get('dscr') if out.get('dscr') is not None else 'N/A'}")
    lines.append(f"• NOI Required (DSCR {inp.dscr_target}): {money(out.get('noi_required'))}")

    lines.append("\n**Offre / Valeur**")
    lines.append(f"• Offre maximale ({inp.cap_target_offer*100:.2f}% cap): {money(out.get('offre_max'))}")
    lines.append(f"• Valeur: {money(out.get('valeur'))}")
    lines.append(f"• $ Refinancé: {money(out.get('refinance_cash'))}")

    lines.append("\n**Cash Flow**")
    lines.append(f"• Cash Flow: {money(out.get('cashflow'))}")

    return "\n".join(lines)
