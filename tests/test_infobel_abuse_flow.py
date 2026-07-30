from reswip_leads.sources.infobel.collect_links import _wait_for_results_or_abuse


class _Page:
    def __init__(self, url: str):
        self.url = url

    def wait_for_function(self, *_args, **_kwargs):
        return None


def test_detects_abuse_redirect_before_results_timeout():
    assert _wait_for_results_or_abuse(_Page("https://www.infobel.com/Landing/Abuse")) == "abuse"


def test_detects_business_results_before_results_timeout():
    assert _wait_for_results_or_abuse(_Page("https://www.infobel.com/BusinessResults")) == "results"
