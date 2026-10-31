"""Bundled, dated catalog of jurisdiction presets (data only).

Each preset covers ONE transaction scope (jurisdiction x purchase type x
occupancy).  Components are checked against the cited official page where
``verified=True``; a profile is never labelled fully ``verified`` while it
omits reliefs, regimes or recurring taxes.  Recurring property taxes are left
for the user because they depend on assessed/cadastral values and municipal
rates; a zero there does NOT mean the tax is legally zero.

The component rates below come from maintainer-supplied research dated
2026-10-31; they were not re-fetched when this file was written.  Planning
estimates only: not legal or tax advice.
"""

from property_jurisdiction import (
    AcquisitionComponent as C,
    PropertyJurisdictionProfile,
)

GENERIC_PROFILE_NAME = "Generic / manual (no jurisdiction estimates)"
CATALOG_AS_OF = "2026-10-31"

_NOT_MODELLED = (
    "Agent commissions, lender fees, municipal surcharges, disbursements "
    "and any eligibility rules not named above are not modelled."
)
_RECURRING_NOTE = (
    "Annual property tax depends on an assessed/cadastral value and local "
    "rates; it is left for you to enter and 0 does not mean no tax is due."
)
_MAINTENANCE = dict(annual_maintenance_percentage=1.0, maintenance_basis="indicative")


def _profile(name, profile_id, scope, purchase_type, occupancy, status,
             unsupported, url, components, notes="", extra_sources=()):
    return PropertyJurisdictionProfile(
        name,
        profile_id=profile_id, version=2, verification_status=status,
        transaction_scope=scope, purchase_type=purchase_type, occupancy=occupancy,
        unsupported_cases=f"{unsupported} {_NOT_MODELLED}",
        source_url=url, additional_sources=tuple(extra_sources),
        data_as_of=CATALOG_AS_OF, property_tax_base="assessed_value",
        notes=f"{notes} {_RECURRING_NOTE}".strip(),
        acquisition_components=tuple(components), **_MAINTENANCE,
    )


_NL = "https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/prive/woning/overdrachtsbelasting/tarieven_overdrachtsbelasting/"
_NL_STARTER = "https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/prive/woning/overdrachtsbelasting/startersvrijstelling/startersvrijstelling"
_AT = "https://www.bmf.gv.at/themen/steuern/immobilien-grundstuecke/grunderwerbsteuer/steuersatz.html"
_AT_PLEDGE = "https://eausweise.oesterreich.gv.at/de/themen/bauen_und_wohnen/wohnen/8/Seite.210150"
_MADRID = "https://www.comunidad.madrid/atencion-contribuyente/transmisiones-patrimoniales-onerosas"
_MADRID_2 = "https://administracion.gob.es/en/tu-espacio-europeo/derechos-obligaciones/ciudadanos/residencia/compraventa-bienes-inmuebles/impuestos"
_CAT = "https://atc.gencat.cat/es/tributs/itpajd/operacions/immobles/compravenda-immobles/"
_LAZIO = "https://www1.agenziaentrate.gov.it/web_app_entrate/guida_acquisto_casa.html"
_FR = "https://www.impots.gouv.fr/particulier/questions/jachete-un-bien-immobilier-quaurai-je-payer-comme-frais-au-notaire"
_BAVARIA = "https://www.lfst.bayern.de/steuerinfos/haeufig-gestellte-fragen/grunderwerbsteuer"
_DE_GRUNDSTEUER = "https://www.bundesfinanzministerium.de/Content/DE/FAQ/faq-die-neue-grundsteuer.html"
_PT = "https://info.portaldasfinancas.gov.pt/pt/informacao_fiscal/codigos_tributarios/cimt/Pages/cimt17.aspx"
_PT_2 = "https://info.portaldasfinancas.gov.pt/pt/apoio_ao_contribuinte/Cidadaos/Casa_e_propriedades/Compra_da_casa/Paginas/default.aspx"
_PT_IMI = "https://www.portaldasfinancas.gov.pt/pt/EP/Pat/main.jsp?body=%2Fimi%2FconsultarTaxasIMIForm.jsp"

_PT_IMT_HPP = (
    (106_346, 0.0, False), (145_470, 2.0, False), (198_347, 5.0, False),
    (330_539, 7.0, False), (660_982, 8.0, False),
    (1_150_853, 6.0, True), (None, 7.5, True),
)
_FR_NOTARY = (
    (6_500, 3.870, False), (17_000, 1.596, False), (60_000, 1.064, False),
    (None, 0.799, False),
)
_NOTARY_NOTE = (
    "Regulated notary tariff before VAT and disbursements, which are not included."
)


def _fr_existing(combined, url_note):
    return (
        C("Transfer taxes (département + commune + state levy)", "statutory",
          rate_percentage=combined, verified=True, source_url=_FR, note=url_note),
        C("Real-estate security contribution", "statutory", rate_percentage=0.1,
          minimum=15.0, verified=True, source_url=_FR),
        C("Notary remuneration (progressive tariff)", "statutory",
          method="brackets", brackets=_FR_NOTARY, verified=True,
          source_url=_FR, note=_NOTARY_NOTE),
    )


def default_property_jurisdiction_profiles():
    """Return the editable catalog offered by the UI.

    The first entry is an explicit all-zero manual profile used when a
    jurisdiction or transaction type is not covered.
    """
    return [
        PropertyJurisdictionProfile(
            GENERIC_PROFILE_NAME, profile_id="generic-manual", version=1,
            transaction_scope="Any; every value is a user assumption.",
            occupancy="any",
            notes="Use when the jurisdiction or purchase type is not covered.",
        ),
        _profile(
            "Netherlands — resale, owner-occupied", "nl-resale-owner-occupied",
            "Existing residential home bought as main residence: 2% transfer tax.",
            "resale", "primary_home", "partially_verified",
            "Starter exemption (see separate preset), non-residential property "
            "(10.4%), new builds under VAT.", _NL,
            [C("Transfer tax (overdrachtsbelasting)", "statutory",
               rate_percentage=2.0, verified=True, source_url=_NL)],
        ),
        _profile(
            "Netherlands — resale, second home / rental / investor",
            "nl-resale-investor",
            "Existing residential property not occupied by the buyer: 8% from 2026.",
            "resale", "secondary_or_investment", "partially_verified",
            "Non-residential property (10.4%), new builds under VAT.", _NL,
            [C("Transfer tax (non-owner-occupied residential)", "statutory",
               rate_percentage=8.0, verified=True, source_url=_NL)],
        ),
        _profile(
            "Netherlands — resale, starter exemption (under 35)",
            "nl-resale-starter",
            "Owner-occupier under 35 using the exemption for the first time: 0% "
            "if property value is at most EUR 555,000 (2026), otherwise 2%.",
            "resale", "primary_home", "partially_verified",
            "Eligibility (age, first use, owner-occupation) is NOT checked by "
            "the simulator; confirm it on the official page.", _NL_STARTER,
            [C("Transfer tax with starter exemption", "statutory",
               method="brackets", brackets=((555_000, 0.0, True), (None, 2.0, True)),
               verified=True, source_url=_NL_STARTER)],
            extra_sources=[_NL],
        ),
        _profile(
            "Austria — resale, ordinary purchase", "at-resale",
            "Purchase for consideration: Grunderwerbsteuer and land-register "
            "ownership entry are separate lines; optional mortgage-pledge "
            "charges apply only when financed.",
            "resale", "any", "partially_verified",
            "Family transfers and temporary exemptions need separate "
            "eligibility checks; signature certification is extra.", _AT,
            [C("Grunderwerbsteuer (transfer tax)", "statutory",
               rate_percentage=3.5, verified=True, source_url=_AT),
             C("Land-register ownership entry", "statutory",
               rate_percentage=1.1, verified=True, source_url=_AT_PLEDGE),
             C("Mortgage pledge registration", "statutory",
               rate_percentage=1.2, base="mortgage_amount", condition="mortgage",
               verified=True, source_url=_AT_PLEDGE),
             C("Pledge application fee", "statutory", method="fixed", amount=85.0,
               condition="mortgage", verified=True, source_url=_AT_PLEDGE),
             C("Legal / notary fees (budget allowance)", "indicative",
               rate_percentage=2.0,
               note="Sources indicate roughly 1-3% of price; 2% is a midpoint "
                    "planning allowance, not a statutory rate.")],
            extra_sources=[_AT_PLEDGE],
        ),
        _profile(
            "Madrid (Spain) — resale, general rate", "es-madrid-resale",
            "Resale dwelling: general regional ITP rate of 6%.",
            "resale", "any", "partially_verified",
            "New housing (10% VAT plus regional AJD), other reliefs.", _MADRID,
            [C("ITP (transmisiones patrimoniales)", "statutory",
               rate_percentage=6.0, verified=True, source_url=_MADRID)],
            extra_sources=[_MADRID_2],
        ),
        _profile(
            "Madrid (Spain) — resale, habitual home up to EUR 250,000",
            "es-madrid-resale-habitual",
            "Habitual-home acquisition up to EUR 250,000: 10% relief on the tax "
            "due (effective 5.4%); above the limit the general 6% applies.",
            "resale", "primary_home", "partially_verified",
            "Eligibility conditions are NOT checked by the simulator; confirm "
            "them on the official page. New housing and other reliefs.", _MADRID,
            [C("ITP after 10% relief", "statutory", method="brackets",
               brackets=((250_000, 5.4, True), (None, 6.0, True)),
               verified=True, source_url=_MADRID)],
            extra_sources=[_MADRID_2],
        ),
        _profile(
            "Catalonia (Spain) — resale, general ITP", "es-catalonia-resale",
            "Resale dwelling: progressive marginal ITP bands (10% to EUR 600k, "
            "11% to 900k, 12% to 1.5m, 13% above).",
            "resale", "any", "partially_verified",
            "Reduced rates (5%, 7%...), the 20% large-holder/whole-building "
            "regime, new dwellings (VAT/AJD).", _CAT,
            [C("ITP (progressive bands)", "statutory", method="brackets",
               brackets=((600_000, 10.0, False), (900_000, 11.0, False),
                         (1_500_000, 12.0, False), (None, 13.0, False)),
               verified=True, source_url=_CAT)],
        ),
        _profile(
            "Lazio (Italy) — private resale, prima casa", "it-lazio-resale-primary",
            "Qualified prima casa: registration tax 2% (minimum EUR 1,000) plus "
            "EUR 50 mortgage and EUR 50 cadastral taxes. Enter the cadastral "
            "income (rendita) to use the prezzo-valore base of income x 1.05 x "
            "110; without it the sale price is used as an indicative proxy.",
            "resale", "primary_home", "partially_verified",
            "Developer/VAT purchases (different rates, EUR 200 fixed taxes), "
            "buyers not qualifying for prima casa.", _LAZIO,
            [C("Registration tax (prima casa)", "statutory", rate_percentage=2.0,
               minimum=1_000.0, base="cadastral_value", base_multiplier=1.05 * 110,
               verified=True, source_url=_LAZIO,
               note="Cadastral base applies only under prezzo-valore."),
             C("Mortgage registry tax", "statutory", method="fixed", amount=50.0,
               verified=True, source_url=_LAZIO),
             C("Cadastral tax", "statutory", method="fixed", amount=50.0,
               verified=True, source_url=_LAZIO)],
            notes="IMU is generally exempt on a non-luxury main home.",
        ),
        _profile(
            "Lazio (Italy) — private resale, second home", "it-lazio-resale-second",
            "Without prima-casa relief: registration tax 9% (minimum EUR 1,000) "
            "plus EUR 50 + EUR 50. Prezzo-valore base: cadastral income x 1.05 "
            "x 120; without it the sale price is used as an indicative proxy.",
            "resale", "secondary_or_investment", "partially_verified",
            "Developer/VAT purchases, luxury-home rules.", _LAZIO,
            [C("Registration tax (no relief)", "statutory", rate_percentage=9.0,
               minimum=1_000.0, base="cadastral_value", base_multiplier=1.05 * 120,
               verified=True, source_url=_LAZIO,
               note="Cadastral base applies only under prezzo-valore."),
             C("Mortgage registry tax", "statutory", method="fixed", amount=50.0,
               verified=True, source_url=_LAZIO),
             C("Cadastral tax", "statutory", method="fixed", amount=50.0,
               verified=True, source_url=_LAZIO)],
        ),
        _profile(
            "France (metropolitan) — existing property, département rate 4.5%",
            "fr-existing-4-5",
            "Existing property where the département keeps the 4.5% component "
            "(combined transfer taxes 5.80665%). The rate varies by département.",
            "resale", "any", "partially_verified",
            "New property, first-time-buyer protections, notary VAT and "
            "disbursements, departments at other rates.", _FR,
            _fr_existing(5.80665, "Département rate varies; check yours."),
        ),
        _profile(
            "France (metropolitan) — existing property, département rate 5%",
            "fr-existing-5",
            "Existing property where the département applies the 5% component "
            "allowed from 2025-04-01 to 2028-03-31 (combined 6.3185%).",
            "resale", "any", "partially_verified",
            "New property, first-time-buyer protections, notary VAT and "
            "disbursements, departments at other rates.", _FR,
            _fr_existing(6.3185, "Département rate varies; check yours."),
        ),
        _profile(
            "France (metropolitan) — new property (indicative)", "fr-new",
            "New property: reduced land-registration tax of about 0.71%.",
            "new_build", "any", "unverified",
            "VAT and other charges, notary remuneration.", _FR,
            [C("Reduced land-registration tax (approx.)", "indicative",
               rate_percentage=0.71, source_url=_FR,
               note="Source says about 0.71%, excluding other charges and VAT.")],
        ),
        _profile(
            "Bavaria (Germany) — resale", "de-bavaria-resale",
            "Resale: 3.5% Grunderwerbsteuer on consideration, plus a separate "
            "indicative allowance for notary and land-register fees.",
            "resale", "any", "partially_verified",
            "Reliefs and exemptions.", _BAVARIA,
            [C("Grunderwerbsteuer (transfer tax)", "statutory",
               rate_percentage=3.5, verified=True, source_url=_BAVARIA),
             C("Notary and land-register fees (budget allowance)", "indicative",
               rate_percentage=2.0,
               note="Planning allowance, not a statutory rate.")],
            notes="Grundsteuer follows the regional model and municipal multiplier.",
            extra_sources=[_DE_GRUNDSTEUER],
        ),
        _profile(
            "Mainland Portugal — resale, primary residence (HPP)", "pt-resale-hpp",
            "2026 IMT bands for a primary residence (marginal up to EUR "
            "660,982, then flat 6% / 7.5% on the whole base) plus 0.8% stamp "
            "duty, both on the higher of price and assessed value (VPT).",
            "resale", "primary_home", "partially_verified",
            "Secondary-home table, young first-home relief, non-resident "
            "rules, mortgage stamp duty. Enter the VPT or the price is used.",
            _PT,
            [C("IMT (primary-residence bands)", "statutory", method="brackets",
               brackets=_PT_IMT_HPP, base="higher_of_price_and_assessed",
               verified=True, source_url=_PT),
             C("Stamp duty", "statutory", rate_percentage=0.8,
               base="higher_of_price_and_assessed", verified=True, source_url=_PT)],
            notes="IMI is roughly 0.3-0.45% of the assessed VPT by municipality; "
                  "not estimated here.",
            extra_sources=[_PT_2, _PT_IMI],
        ),
    ]


def preset_update_notices(profiles):
    """Describe catalog presets that are newer than the profiles supplied.

    Scenario profiles are never rewritten; callers show these notices and let
    the user add the updated preset deliberately.
    """
    current = {
        item.profile_id: item for item in default_property_jurisdiction_profiles()
        if item.profile_id
    }
    return [
        f"'{profile.name}' uses catalog version {profile.version}; version "
        f"{current[profile.profile_id].version} is available. Your saved "
        "values were left unchanged."
        for profile in profiles
        if profile.profile_id in current
        and profile.version < current[profile.profile_id].version
    ]
