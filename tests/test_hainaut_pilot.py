import csv

from scripts.run_hainaut_three_row_pilot import select_pilot_rows
from reswip_leads.sources.iqualif.importer import IQualifImporter
from reswip_leads.sources.infobel.scrape_urls import _is_skipped_external
from reswip_leads.enrichment.pappers import _EXCLUDED_WEBSITE_DOMAINS
from reswip_leads.sources.infobel.pipeline import _read_tva_values
from reswip_leads.sources.infobel.pipeline import _run_tva_batch


def test_select_pilot_rows_reads_iqualif_utf8_bom_and_returns_three_rows(tmp_path):
    source = tmp_path / "source.csv"
    rows = [
        {
            "Sector Activity": "Energy",
            "Company Name": "One",
            "TVA Number": "BE0670252875",
            "Province": "Hainaut",
        },
        {
            "Sector Activity": "Energy",
            "Company Name": "Two",
            "TVA Number": "BE0423076980",
            "Province": "Hainaut",
        },
        {
            "Sector Activity": "Energy",
            "Company Name": "Three",
            "TVA Number": "BE0670360169",
            "Province": "Hainaut",
        },
        {
            "Sector Activity": "Energy",
            "Company Name": "Four",
            "TVA Number": "",
            "Province": "Hainaut",
        },
    ]
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0], delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    original = source.read_bytes()

    selected = select_pilot_rows(source, limit=3)

    assert len(selected) == 3
    assert [row["TVA Number"] for row in selected] == [
        "BE0670252875",
        "BE0423076980",
        "BE0670360169",
    ]
    assert source.read_bytes() == original


def test_select_pilot_rows_rejects_sources_with_fewer_valid_tvas(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        "\ufeffCompany Name;TVA Number\nOnly;BE0670252875\n",
        encoding="utf-8",
    )

    try:
        select_pilot_rows(source, limit=3)
    except ValueError as exc:
        assert "3 valid TVA" in str(exc)
    else:
        raise AssertionError("expected ValueError for an undersized pilot")


def test_select_pilot_rows_supports_twenty_row_batch(tmp_path):
    source = tmp_path / "source.csv"
    fieldnames = ["Company Name", "TVA Number"]
    with source.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for number in range(20):
            writer.writerow(
                {
                    "Company Name": f"Company {number}",
                    "TVA Number": f"BE0123456{number:03d}",
                }
            )

    rows = select_pilot_rows(source, limit=20)

    assert len(rows) == 20
    assert rows[-1]["Company Name"] == "Company 19"


def test_select_pilot_rows_full_mode_keeps_named_rows_without_tva(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        "\ufeffCompany Name;TVA Number\nOne;BE0123456789\nTwo;\n",
        encoding="utf-8",
    )

    rows = select_pilot_rows(source, limit=2, include_invalid_tva=True)

    assert len(rows) == 2
    assert rows[1]["Company Name"] == "Two"
    assert rows[1]["TVA Number"] == ""


def test_iqualif_importer_preserves_tva_number_column(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        "\ufeffCompany Name;TVA Number;Province\n"
        "One;BE0670252875;Hainaut\n",
        encoding="utf-8",
    )

    leads = IQualifImporter().import_leads([str(source)])

    assert len(leads) == 1
    assert leads[0].tva == "BE0670252875"


def test_iqualif_importer_preserves_company_state(tmp_path):
    source = tmp_path / "source.csv"
    source.write_text(
        "\ufeffCompany Name;TVA Number;State\nOne;BE0670252875;Actif\n",
        encoding="utf-8",
    )

    leads = IQualifImporter().import_leads([str(source)])

    assert leads[0].status == "Actif"


def test_infobel_skips_generic_directory_links_as_websites():
    assert _is_skipped_external("https://search.infobelpro.com/belgium/fr/hainaut/celles")
    assert _is_skipped_external("https://ejustice.just.fgov.be/tsv_pdf/example.pdf")


def test_pappers_skips_government_links_as_websites():
    assert "ejustice.just.fgov.be" in _EXCLUDED_WEBSITE_DOMAINS


def test_infobel_tva_batch_reader_accepts_semicolon_iqualif_csv(tmp_path):
    source = tmp_path / "pilot.csv"
    source.write_text(
        "\ufeffCompany Name;TVA Number\nOne;BE0670252875\n",
        encoding="utf-8",
    )

    assert _read_tva_values(source) == ["0670252875"]


def test_infobel_tva_batch_uses_one_batch_link_collection(tmp_path, monkeypatch):
    source = tmp_path / "input.csv"
    source.write_text(
        "tva\n0670252875\n0423076980\n0670360169\n",
        encoding="utf-8",
    )
    calls = []

    def fake_collect(tvas, *, headed, profile_dir, on_row=None):
        calls.append(list(tvas))
        rows = [
            {
                "search_tva": tvas[0],
                "infobel_url": "https://example.test/one",
                "business_name": "One",
            },
        ]
        if on_row:
            on_row(rows[0])
        return rows

    monkeypatch.setattr(
        "reswip_leads.sources.infobel.collect_links.collect_tva_links",
        fake_collect,
    )
    result = _run_tva_batch(
        str(source),
        str(tmp_path / "output.csv"),
        headed=False,
        limit=None,
        profile_dir="/tmp/profile",
    )

    assert result == 0
    assert calls == [["0670252875", "0423076980", "0670360169"]]
