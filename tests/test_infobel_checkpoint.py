import csv

from reswip_leads.sources.infobel.pipeline import (
    _load_checkpoint_rows,
    _write_checkpoint_rows,
)


def test_checkpoint_round_trip_preserves_rows(tmp_path):
    path = tmp_path / "infobel.csv"
    rows = [
        {
            "search_tva": "0460782662",
            "business_name": "Actibel Immobilier",
            "infobel_status": "scraped",
        },
        {
            "search_tva": "0867766354",
            "infobel_status": "no_result",
        },
    ]

    _write_checkpoint_rows(path, rows)

    loaded = _load_checkpoint_rows(path)
    assert loaded[0]["search_tva"] == rows[0]["search_tva"]
    assert loaded[0]["business_name"] == rows[0]["business_name"]
    assert loaded[1]["infobel_status"] == rows[1]["infobel_status"]
    with path.open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle))[0]["search_tva"] == "0460782662"
