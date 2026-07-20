from pathlib import Path
from tempfile import NamedTemporaryFile
from io import BytesIO

import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError

from vision_service.analyzer import CatalogAnalyzer
from vision_service.config import settings
from vision_service.schemas import CatalogAnalysisResponse
from vision_service.catalog_matcher import CatalogImageMatcher
from vision_service.schemas import CatalogIndexResponse


app = FastAPI(title="SafeMaint Offline Vision Service", version="0.1.0")
analyzer = CatalogAnalyzer(settings)
matcher = CatalogImageMatcher(
    settings.catalog_index_dir,
    settings.catalog_match_threshold,
    max_pages=settings.pdf_max_pages,
    max_images=settings.catalog_max_images,
    max_image_pixels=settings.image_max_pixels,
    embedding_model=settings.embedding_model,
    embedding_device=settings.embedding_device,
    model_cache_dir=settings.model_cache_dir,
)
SUPPORTED_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _read_limited(file: UploadFile, limit: int) -> bytes:
    content = file.file.read(limit + 1)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일은 처리할 수 없습니다.")
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"파일 크기는 {limit} 바이트를 넘을 수 없습니다.",
        )
    return content


def _validate_image(content: bytes) -> None:
    try:
        with Image.open(BytesIO(content)) as image:
            if image.width * image.height > settings.image_max_pixels:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"이미지 픽셀 수는 {settings.image_max_pixels}개를 넘을 수 없습니다.",
                )
            image.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise HTTPException(status_code=422, detail="손상되었거나 지원하지 않는 이미지입니다.") from error


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
    content = _read_limited(file, settings.image_max_upload_bytes)
    _validate_image(content)
    suffix = Path(file.filename or "catalog.png").suffix or ".png"
    with NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(content)
        path = Path(temporary.name)
    try:
        return analyzer.analyze(path, file.filename or path.name)
    finally:
        path.unlink(missing_ok=True)


@app.post("/v1/catalog/index", response_model=CatalogIndexResponse)
def index_catalog(file: UploadFile = File(...)) -> CatalogIndexResponse:
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="PDF 카탈로그만 등록할 수 있습니다.")
    content = _read_limited(file, settings.pdf_max_upload_bytes)
    if not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="PDF 파일 헤더가 올바르지 않습니다.")
    with NamedTemporaryFile(suffix=".pdf", delete=False) as temporary:
        temporary.write(content)
        path = Path(temporary.name)
    try:
        try:
            return matcher.index_pdf(path, file.filename or "catalog.pdf")
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
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
    analysis_mode: str = Form("deep"),
) -> CatalogAnalysisResponse:
    if file.content_type not in SUPPORTED_TYPES:
        raise HTTPException(status_code=415, detail="JPEG, PNG, WEBP 이미지만 분석할 수 있습니다.")
    try:
        raw_ids = json.loads(catalog_ids)
    except json.JSONDecodeError:
        raw_ids = [value.strip() for value in catalog_ids.split(",") if value.strip()]
    if not isinstance(raw_ids, list) or len(raw_ids) > 20:
        raise HTTPException(status_code=422, detail="카탈로그 ID는 최대 20개까지 허용됩니다.")
    selected_ids = list(dict.fromkeys(str(value) for value in raw_ids))
    if not all(len(value) == 20 and all(char in "0123456789abcdef" for char in value) for value in selected_ids):
        raise HTTPException(status_code=422, detail="카탈로그 ID 형식이 올바르지 않습니다.")
    if analysis_mode not in {"fast", "deep"}:
        raise HTTPException(status_code=422, detail="analysis_mode는 fast 또는 deep이어야 합니다.")
    content = _read_limited(file, settings.image_max_upload_bytes)
    _validate_image(content)
    suffix = Path(file.filename or "field-image.png").suffix or ".png"
    with NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
        temporary.write(content)
        path = Path(temporary.name)
    try:
        try:
            candidates = matcher.match(path, selected_ids)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if analysis_mode == "fast":
            return CatalogAnalysisResponse(
                filename=file.filename or path.name,
                items=[],
                warnings=["빠른 임베딩 검색 결과이며 정밀 분석이 이어서 진행됩니다."],
                models=[settings.embedding_model],
                catalog_candidates=candidates[:3],
            )
        # Field photos need object recognition first. PaddleOCR-VL is a document parser
        # and can take many minutes on CPU, so keep it for explicit catalog analysis only.
        response = analyzer.analyze(path, file.filename or path.name, include_ocr=False)
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
