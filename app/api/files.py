from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.schemas import InputFileResponse
from app.services.files import FileService


router = APIRouter(prefix="/api/files", tags=["files"])


def get_file_service(settings: Settings = Depends(get_settings)) -> FileService:
    return FileService(settings.input_directory, settings.output_directory, settings.upload_directory)


@router.get("/inputs", response_model=list[InputFileResponse])
def list_inputs(service: FileService = Depends(get_file_service)) -> list[dict[str, object]]:
    return service.list_input_files()


@router.post("/upload", response_model=InputFileResponse, status_code=201)
async def upload_input(
    file: UploadFile = File(...),
    service: FileService = Depends(get_file_service),
) -> dict[str, object]:
    try:
        content = await file.read()
        path = service.save_upload(file.filename or "iqualif.csv", content)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    item = next(item for item in service.list_input_files() if item["path"] == str(path))
    return item
