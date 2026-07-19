from pathlib import Path
from tempfile import NamedTemporaryFile

import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from vision_service.analyzer import CatalogAnalyzer
from vision_service.config import settings
from vision_service.schemas import CatalogAnalysisResponse
from vision_service.catalog_matcher import CatalogImageMatcher
from vision_service.schemas import CatalogIndexResponse


app = FastAPI(title="SafeMaint Offline Vision Service", version="0.1.0")
analyzer = CatalogAnalyzer(settings)
matcher = CatalogImageMatcher(settings.catalog_index_dir, settings.catalog_match_threshold)
SUPPORTED_TYPES = {"image/jpeg", "image/png", "image/webp"}


@app.get("/health/live")
def live() -> dict[str, object]:
    return {
        "status": "ok",
        "device": settings.device,
        "paddle_enabled": settings.enable_paddle,
        "qwen_enabled": settings.enable_qwen,
    }


@app.post("/v1/catalog/analyze", response_model=CatalogAnalysisResponse)
def analyze_catalog(file: UploadFile = File(...)) -> CatalogAnalysisResponse:
    if file.content_type not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="JPEG, PNG, WEBP 이미지만 분석할 수 있습니다.",
        )
    suffix = Path(file.filename or "catalog.png").suffix or ".png"
    with NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(file.file.read())
        path = Path(temporary.name)
    try:
        return analyzer.analyze(path, file.filename or path.name)
    finally:
        path.unlink(missing_ok=True)


@app.post("/v1/catalog/index", response_model=CatalogIndexResponse)
def index_catalog(file: UploadFile = File(...)) -> CatalogIndexResponse:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="PDF 카탈로그만 등록할 수 있습니다.")
    with NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
        temporary.write(file.file.read())
        path = Path(temporary.name)
    try:
        return matcher.index_pdf(path, file.filename or "catalog.pdf")
    finally:
        path.unlink(missing_ok=True)


@app.get("/v1/catalog/image/{catalog_id}/{page}/{image_index}", response_class=FileResponse)
def catalog_image(catalog_id: str, page: int, image_index: int) -> FileResponse:
    if len(catalog_id) != 20 or any(char not in "0123456789abcdef" for char in catalog_id):
        raise HTTPException(status_code=404, detail="후보 이미지를 찾을 수 없습니다.")
    path = matcher.resolve_image(catalog_id, page, image_index)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="후보 이미지를 찾을 수 없습니다.")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.post("/v1/catalog/match", response_model=CatalogAnalysisResponse)
def match_catalog(
    file: UploadFile = File(...),
    catalog_ids: str = Form("[]"),
) -> CatalogAnalysisResponse:
    if file.content_type not in SUPPORTED_TYPES:
        raise HTTPException(status_code=415, detail="JPEG, PNG, WEBP 이미지만 분석할 수 있습니다.")
    try:
        selected_ids = [str(value) for value in json.loads(catalog_ids)]
    except (json.JSONDecodeError, TypeError):
        selected_ids = [value.strip() for value in catalog_ids.split(",") if value.strip()]
    if not all(len(value) == 20 and all(char in "0123456789abcdef" for char in value) for value in selected_ids):
        raise HTTPException(status_code=422, detail="카탈로그 ID 형식이 올바르지 않습니다.")
    suffix = Path(file.filename or "field-image.png").suffix or ".png"
    with NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(file.file.read())
        path = Path(temporary.name)
    try:
        response = analyzer.analyze(path, file.filename or path.name)
        candidates = matcher.match(path, selected_ids)
        candidate_pairs = [(candidate, matcher.image_path(candidate)) for candidate in candidates]
        candidate_pairs = [(candidate, image) for candidate, image in candidate_pairs if image is not None]
        verified = analyzer.rerank_catalog_candidates(
            path,
            [candidate for candidate, _ in candidate_pairs],
            [image for _, image in candidate_pairs],
            next((
                item.component_name or item.equipment_type
                for item in response.items
                if item.component_name or item.equipment_type
            ), None),
        )
        return response.model_copy(update={"catalog_candidates": verified})
    finally:
        path.unlink(missing_ok=True)
