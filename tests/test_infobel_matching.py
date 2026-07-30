from reswip_leads.sources.infobel.collect_links import select_best_candidate


def test_selects_matching_company_when_tva_returns_multiple_same_address_results():
    candidates = [
        {
            "business_name": "Business Village Ecolys by Actibel",
            "address": "Avenue d'Ecolys 2",
            "postal_code": "5020",
            "city": "Namur",
            "financial_tva": "BE0460782662",
        },
        {
            "business_name": "Actibel Immobilier",
            "address": "Avenue d'Ecolys 2",
            "postal_code": "5020",
            "city": "Namur",
            "financial_tva": "BE0460782662",
        },
    ]

    selected = select_best_candidate(
        candidates,
        {
            "tva": "BE0460782662",
            "company_name": "ACTIBEL IMMOBILIER",
            "address": "Avenue d'Ecolys(RH) 2 2",
            "postal_code": "5020",
            "city": "Namur",
        },
    )

    assert selected == candidates[1]
