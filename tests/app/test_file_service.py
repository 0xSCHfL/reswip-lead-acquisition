from pathlib import Path

from app.services.files import FileService


def test_input_listing_and_path_escape(tmp_path: Path):
    inputs = tmp_path / "inputs"
    outputs = tmp_path / "outputs"
    inputs.mkdir()
    (inputs / "leads.csv").write_text("Company Name,VAT Number\nDemo,BE0123456789\n")
    (tmp_path / "secret.csv").write_text("secret")
    service = FileService(inputs, outputs)

    assert [item["name"] for item in service.list_input_files()] == ["leads.csv"]
    assert service.validate_input_path(inputs / "leads.csv") is True
    assert service.validate_input_path(tmp_path / "secret.csv") is False


def test_upload_is_saved_with_safe_unique_name(tmp_path: Path):
    service = FileService(tmp_path / "inputs", tmp_path / "outputs", tmp_path / "uploads")
    saved = service.save_upload("my full iqualif db.csv", b"Company Name\nDemo\n")
    assert saved.parent == (tmp_path / "uploads").resolve()
    assert saved.suffix == ".csv"
    assert " " not in saved.name
    assert saved.read_bytes().startswith(b"Company Name")
    assert any(item["path"] == str(saved) for item in service.list_input_files())
