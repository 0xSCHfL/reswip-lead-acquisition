from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


ALLOWED_INPUT_SUFFIXES = {".csv", ".xlsx"}


class FileService:
    def __init__(self, input_dir: Path, output_dir: Path, upload_dir: Path | None = None) -> None:
        self.input_dir = input_dir.expanduser().resolve()
        self.output_dir = output_dir.expanduser().resolve()
        self.upload_dir = (upload_dir or input_dir).expanduser().resolve()

    def list_input_files(self) -> list[dict[str, object]]:
        roots = [self.input_dir]
        if self.upload_dir != self.input_dir:
            roots.append(self.upload_dir)
        if not any(root.is_dir() for root in roots):
            return []
        files = []
        candidates = []
        seen: set[Path] = set()
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path in seen or not path.is_file() or path.suffix.lower() not in ALLOWED_INPUT_SUFFIXES:
                    continue
                seen.add(path)
                stat = path.stat()
                candidates.append((stat.st_mtime, {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                }))
        return [item for _, item in sorted(candidates, key=lambda pair: pair[0], reverse=True)]

    def validate_input_path(self, path: Path) -> bool:
        try:
            resolved = path.expanduser().resolve(strict=True)
        except FileNotFoundError:
            return False
        allowed_roots = (self.input_dir, self.upload_dir)
        return resolved.is_file() and resolved.suffix.lower() in ALLOWED_INPUT_SUFFIXES and any(self._inside(resolved, root) for root in allowed_roots)

    def save_upload(self, filename: str, content: bytes) -> Path:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in ALLOWED_INPUT_SUFFIXES:
            raise ValueError("only CSV and XLSX files are supported")
        if len(content) == 0:
            raise ValueError("uploaded file is empty")
        safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", Path(filename).stem).strip("_") or "iqualif"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        destination = self.upload_dir / f"{safe_stem}-{uuid.uuid4().hex[:12]}{suffix}"
        destination.write_bytes(content)
        return destination

    def output_dir_for(self, job_id: str) -> Path:
        if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
            raise ValueError("invalid job id")
        path = self.output_dir / job_id
        path.mkdir(parents=True, exist_ok=False)
        return path

    @staticmethod
    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _inside(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
        except ValueError:
            return False
        return True
