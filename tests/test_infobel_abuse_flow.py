from reswip_leads.sources.infobel.collect_links import (
    _infobel_search_location,
    _wait_for_results_or_abuse,
)
from reswip_leads.sources.infobel.scrape_urls import _is_skipped_external


class _Page:
    def __init__(self, url: str):
        self.url = url

    def wait_for_function(self, *_args, **_kwargs):
        return None


def test_detects_abuse_redirect_before_results_timeout():
    assert _wait_for_results_or_abuse(_Page("https://www.infobel.com/Landing/Abuse")) == "abuse"


def test_detects_business_results_before_results_timeout():
    assert _wait_for_results_or_abuse(_Page("https://www.infobel.com/BusinessResults")) == "results"


def test_infobel_search_location_prefers_postcode_then_city_then_address():
    assert _infobel_search_location({"postal_code": "7910", "city": "Hainaut"}) == "7910"
    assert _infobel_search_location({"city": "Hainaut", "address": "Rue 1"}) == "Hainaut"
    assert _infobel_search_location({"address": "Rue 1"}) == "Rue 1"


def test_skips_generic_sirdata_tracking_website():
    assert _is_skipped_external("https://cmp.sirdata.com/") is True
