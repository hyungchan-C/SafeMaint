from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from vision_service.schemas import CatalogCandidate, CatalogIndexResponse


@dataclass(frozen=True, slots=True)
class CatalogMatchSignals:
    candidates: list[CatalogCandidate]
    has_visible_text: bool
    top_similarity: float
    similarity_margin: float
    visual_category: str = ""
    visual_features: tuple[str, ...] = ()


class _SemanticEmbedder:
    def __init__(self, model_name: str, device: str, cache_dir: str) -> None:
        self.model_name = model_name
        self.device = device
        self.cache_dir = cache_dir
        self._model = None
        self._processor = None
        self._text_vectors: np.ndarray | None = None
        self._text_presence_vectors: np.ndarray | None = None

    def encode(self, image: Image.Image) -> np.ndarray:
        return self.encode_many([image])[0]

    def encode_many(self, images: list[Image.Image]) -> np.ndarray:
        import torch
        from transformers import AutoModel, AutoProcessor

        if self._model is None:
            self._processor = AutoProcessor.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir,
                use_fast=True,
            )
            self._model = AutoModel.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir,
            ).eval().to(self.device)
            torch.set_num_threads(min(4, torch.get_num_threads()))
        assert self._processor is not None
        with torch.inference_mode():
            inputs = self._processor(
                images=[image.convert("RGB") for image in images],
                return_tensors="pt",
            )
            inputs = {key: value.to(self.device) for key, value in inputs.items()}
            if hasattr(self._model, "get_image_features"):
                vectors = self._model.get_image_features(**inputs)
            else:
                outputs = self._model(**inputs)
                vectors = outputs.last_hidden_state[:, 0]
        result = vectors.float().cpu().numpy()
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        return result / np.maximum(norms, 1e-12)

    def classify(self, image_vectors: np.ndarray) -> tuple[str, list[str]]:
        """Return a generic shape label using only the local SigLIP2 model."""
        import torch

        labels = (
            ("사진상 육각 머리 볼트", "육각형 머리와 나사산이 보임"),
            ("사진상 둥근 머리 내부 육각 소켓 나사", "둥근 머리와 내부 육각 홈이 보임"),
            ("사진상 원통 머리 내부 육각 소켓 나사", "원통형 머리와 내부 육각 홈이 보임"),
            ("사진상 접시 머리 나사", "머리 윗면이 평평하고 아래쪽이 경사진 형상"),
            ("사진상 십자 또는 일자 홈 나사", "드라이버용 머리 홈이 보임"),
            ("사진상 너트", "중앙 체결 구멍이 있는 다각형 부품"),
            ("사진상 와셔", "얇은 고리 모양 부품"),
            ("사진상 베어링", "동심 원형의 내륜과 외륜이 보임"),
            ("사진상 기어", "둘레에 반복되는 톱니가 보임"),
            ("사진상 산업용 센서", "센서 하우징과 연결부가 보임"),
            ("사진상 산업용 카메라", "렌즈 또는 렌즈 마운트가 있는 카메라 형상"),
            ("사진상 전기 커넥터", "전기 접속용 단자 또는 소켓 형상"),
            ("사진상 밸브", "유체 개폐용 몸체와 연결부 형상"),
            ("사진상 USB 플래시 메모리", "USB 단자와 휴대용 저장장치 몸체가 보임"),
        )
        prompts = [
            "a close-up product photo of a hex head bolt",
            "a close-up product photo of a button head hex socket screw",
            "a close-up product photo of a socket head cap screw",
            "a close-up product photo of a countersunk flat head screw",
            "a close-up product photo of a slotted or Phillips head screw",
            "a close-up product photo of a hex nut",
            "a close-up product photo of a flat washer",
            "a close-up product photo of a ball bearing",
            "a close-up product photo of a mechanical gear",
            "a close-up product photo of an industrial sensor",
            "a close-up product photo of an industrial camera",
            "a close-up product photo of an electrical connector",
            "a close-up product photo of an industrial valve",
            "a close-up product photo of a USB flash drive memory stick",
        ]
        assert self._model is not None and self._processor is not None
        if self._text_vectors is None:
            with torch.inference_mode():
                inputs = self._processor(
                    text=prompts, padding="max_length", return_tensors="pt"
                )
                inputs = {key: value.to(self.device) for key, value in inputs.items()}
                vectors = self._model.get_text_features(**inputs)
            text_vectors = vectors.float().cpu().numpy()
            norms = np.linalg.norm(text_vectors, axis=1, keepdims=True)
            self._text_vectors = text_vectors / np.maximum(norms, 1e-12)
        scores = np.max(image_vectors @ self._text_vectors.T, axis=0)
        best = int(np.argmax(scores))
        return labels[best][0], [labels[best][1], "사진 형상만으로 분류한 추정 결과"]

    def has_visible_text(self, image_vectors: np.ndarray) -> bool:
        """Use the existing SigLIP vectors as a cheap gate before running OCR."""
        import torch

        assert self._model is not None and self._processor is not None
        if self._text_presence_vectors is None:
            prompts = (
                "a close-up product photo with clearly visible printed or engraved letters and numbers",
                "a close-up product photo without any visible letters, numbers, label, or engraving",
            )
            with torch.inference_mode():
                inputs = self._processor(
                    text=list(prompts), padding="max_length", return_tensors="pt"
                )
                inputs = {key: value.to(self.device) for key, value in inputs.items()}
                vectors = self._model.get_text_features(**inputs)
            text_vectors = vectors.float().cpu().numpy()
            norms = np.linalg.norm(text_vectors, axis=1, keepdims=True)
            self._text_presence_vectors = text_vectors / np.maximum(norms, 1e-12)
        scores = np.max(image_vectors @ self._text_presence_vectors.T, axis=0)
        return bool(scores[0] >= scores[1] + 0.02)


def _feature(image: Image.Image) -> np.ndarray:
    """A deterministic, offline descriptor. It deliberately contains no OCR/spec inference."""
    source = image.convert("RGB")
    # Product catalogs commonly use a white background. Cropping that background makes
    # the score less sensitive to page layout while keeping the actual silhouette.
    array = np.asarray(source)
    foreground = np.any(array < 245, axis=2)
    points = np.argwhere(foreground)
    if points.size:
        top, left = points.min(axis=0)
        bottom, right = points.max(axis=0)
        source = source.crop((int(left), int(top), int(right) + 1, int(bottom) + 1))
    contained = ImageOps.contain(source, (224, 224))
    rgb = Image.new("RGB", (256, 256), "white")
    rgb.paste(contained, ((256 - contained.width) // 2, (256 - contained.height) // 2))
    gray = ImageOps.grayscale(rgb).resize((32, 32))
    edges = gray.filter(ImageFilter.FIND_EDGES)
    pixels = np.asarray(gray, dtype=np.float32).reshape(-1) / 255.0
    edge_pixels = np.asarray(edges, dtype=np.float32).reshape(-1) / 255.0
    hist = np.concatenate([
        np.histogram(np.asarray(rgb)[:, :, channel], bins=16, range=(0, 255), density=True)[0]
        for channel in range(3)
    ]).astype(np.float32)
    vector = np.concatenate([pixels, edge_pixels * 1.5, hist * 8.0])
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm else vector


class CatalogImageMatcher:
    def __init__(
        self,
        root: str,
        threshold: float,
        *,
        max_pages: int = 2000,
        max_images: int = 12000,
        max_image_pixels: int = 40_000_000,
        embedding_model: str = "google/siglip2-base-patch16-naflex",
        embedding_device: str = "cpu",
        model_cache_dir: str = "/models",
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold
        self.max_pages = max_pages
        self.max_images = max_images
        self.max_image_pixels = max_image_pixels
        self.embedding_model = embedding_model
        self.embedder = _SemanticEmbedder(embedding_model, embedding_device, model_cache_dir)

    def warmup(self) -> None:
        """Load the embedding model and its small text-classification cache."""
        image = Image.new("RGB", (256, 256), "white")
        vectors = self.embedder.encode_many([image])
        self.embedder.classify(vectors)
        self.embedder.has_visible_text(vectors)

    @staticmethod
    def _query_views(image: Image.Image) -> list[Image.Image]:
        """Return full image plus overlapping detail views for hand-held small parts."""
        width, height = image.size
        crop_width = max(64, int(width * 0.58))
        crop_height = max(64, int(height * 0.58))
        if crop_width >= width or crop_height >= height:
            return [image]
        positions = (
            (0, 0), (width - crop_width, 0),
            (0, height - crop_height), (width - crop_width, height - crop_height),
            ((width - crop_width) // 2, (height - crop_height) // 2),
            ((width - crop_width) // 2, height - crop_height),
        )
        return [image, *(
            image.crop((left, top, left + crop_width, top + crop_height))
            for left, top in positions
        )]

    @staticmethod
    def _page_views(
        image: Image.Image,
    ) -> list[tuple[Image.Image, tuple[int, int, int, int] | None]]:
        """Return a full PDF page and four overlapping page regions."""
        width, height = image.size
        if width < 128 or height < 128:
            return [(image, None)]
        overlap_x = max(16, int(width * 0.08))
        overlap_y = max(16, int(height * 0.08))
        middle_x = width // 2
        middle_y = height // 2
        boxes = (
            (0, 0, min(width, middle_x + overlap_x), min(height, middle_y + overlap_y)),
            (max(0, middle_x - overlap_x), 0, width, min(height, middle_y + overlap_y)),
            (0, max(0, middle_y - overlap_y), min(width, middle_x + overlap_x), height),
            (max(0, middle_x - overlap_x), max(0, middle_y - overlap_y), width, height),
        )
        return [(image, None), *((image.crop(box), box) for box in boxes)]

    def index_pdf(self, pdf_path: Path, filename: str) -> CatalogIndexResponse:
        data = pdf_path.read_bytes()
        index_version = f"safemaint-page-matrix-v5:{self.embedding_model}"
        catalog_id = hashlib.sha256(data + index_version.encode()).hexdigest()[:20]
        target = self.root / catalog_id
        manifest_path = target / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["summary"]["index_version"] = index_version
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            return CatalogIndexResponse(**manifest["summary"])

        try:
            reader = PdfReader(BytesIO(data))
        except (PdfReadError, ValueError, OSError) as error:
            raise ValueError("손상되었거나 지원하지 않는 PDF입니다.") from error
        if len(reader.pages) > self.max_pages:
            raise ValueError(f"PDF 페이지 수는 {self.max_pages}개를 넘을 수 없습니다.")
        target.mkdir(parents=True, exist_ok=True)
        entries: list[dict[str, object]] = []
        embeddings: list[np.ndarray] = []
        warnings: list[str] = []
        try:
            import pypdfium2 as pdfium

            rendered_pdf = pdfium.PdfDocument(str(pdf_path))
        except (ImportError, OSError, ValueError) as error:
            raise ValueError(
                "PDF 페이지 렌더러를 시작하지 못했습니다. pypdfium2 설치 상태를 확인해 주세요."
            ) from error
        for page_number, page in enumerate(reader.pages, start=1):
            if len(entries) >= self.max_images:
                warnings.append(f"이미지는 최대 {self.max_images}개까지만 인덱싱했습니다.")
                break
            page_text = " ".join((page.extract_text() or "").split())[:1600]
            page_image: Image.Image | None = None
            page_image_name = f"page-{page_number:04d}.jpg"
            try:
                rendered_page = rendered_pdf[page_number - 1]
                page_image = rendered_page.render(scale=1.5).to_pil().convert("RGB")
                rendered_page.close()
                if page_image.width * page_image.height > self.max_image_pixels:
                    page_image.thumbnail((2400, 2400))
                page_image.save(target / page_image_name, format="JPEG", quality=88)
                page_views = self._page_views(page_image)
                page_vectors = self.embedder.encode_many([view for view, _ in page_views])
                for view_number, ((_, box), vector) in enumerate(
                    zip(page_views, page_vectors),
                    start=1,
                ):
                    if len(entries) >= self.max_images:
                        break
                    vector_index = len(embeddings)
                    embeddings.append(vector)
                    entries.append({
                        "page": page_number,
                        "image_index": view_number,
                        "image": page_image_name,
                        "vector_index": vector_index,
                        "entry_kind": "page" if box is None else "page_region",
                        "bounding_box": list(box) if box is not None else None,
                        "page_text": page_text,
                    })
            except Exception as exc:
                warnings.append(
                    f"{page_number}페이지 렌더링 실패: {type(exc).__name__}"
                )
            try:
                page_images = list(page.images)
            except Exception as exc:
                warnings.append(f"{page_number}페이지 이미지 추출 실패: {type(exc).__name__}")
                continue
            for image_index, embedded in enumerate(page_images, start=1):
                try:
                    source_image = Image.open(BytesIO(embedded.data))
                    if source_image.width * source_image.height > self.max_image_pixels:
                        warnings.append(f"{page_number}페이지의 과도하게 큰 이미지를 건너뛰었습니다.")
                        continue
                    image = source_image.convert("RGB")
                    if image.width < 64 or image.height < 64:
                        continue
                    extracted_name = f"p{page_number:04d}-i{image_index:03d}.jpg"
                    image.save(target / extracted_name, format="JPEG", quality=90)
                    vector_index = len(embeddings)
                    embeddings.append(self.embedder.encode(image))
                    entries.append({
                        "page": page_number, "image_index": 1000 + image_index,
                        "image": page_image_name if page_image is not None else extracted_name,
                        "vector_index": vector_index,
                        "entry_kind": "embedded_image",
                        "page_text": page_text,
                    })
                except Exception:
                    continue
        rendered_pdf.close()
        summary = {
            "catalog_id": catalog_id, "index_version": index_version, "filename": filename,
            "page_count": len(reader.pages), "image_count": len(entries),
            "warnings": warnings,
        }
        if embeddings:
            matrix = np.stack(embeddings).astype(np.float32, copy=False)
            np.save(target / "embeddings.npy", matrix, allow_pickle=False)
        manifest_path.write_text(
            json.dumps({"summary": summary, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        return CatalogIndexResponse(**summary)

    def match_with_signals(
        self, image_path: Path, catalog_ids: list[str], limit: int = 8
    ) -> CatalogMatchSignals:
        query_image = Image.open(image_path)
        if query_image.width * query_image.height > self.max_image_pixels:
            raise ValueError(f"이미지 픽셀 수는 {self.max_image_pixels}개를 넘을 수 없습니다.")
        queries = self.embedder.encode_many(self._query_views(query_image))
        visual_category, visual_features = self.embedder.classify(queries)
        has_visible_text = self.embedder.has_visible_text(queries)
        scored: list[CatalogCandidate] = []
        for catalog_id in catalog_ids:
            manifest_path = self.root / catalog_id / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            filename = manifest["summary"]["filename"]
            entries = manifest["entries"][: self.max_images]
            matrix_path = self.root / catalog_id / "embeddings.npy"
            if matrix_path.exists():
                matrix = np.load(matrix_path, mmap_mode="r", allow_pickle=False)
                if matrix.ndim != 2 or matrix.shape[1] != queries.shape[1] or matrix.shape[0] < len(entries):
                    continue
                similarities = np.asarray(
                    np.max(matrix[: len(entries)] @ queries.T, axis=1), dtype=np.float32
                )
            else:
                # Compatibility for indexes created before the matrix format.
                legacy_vectors = []
                compatible_entries = []
                for entry in entries:
                    feature = entry.get("feature")
                    if not feature:
                        continue
                    candidate = np.load(self.root / catalog_id / str(feature), allow_pickle=False)
                    if candidate.shape == queries.shape[1:]:
                        legacy_vectors.append(candidate)
                        compatible_entries.append(entry)
                if not legacy_vectors:
                    continue
                entries = compatible_entries
                similarities = np.max(np.stack(legacy_vectors) @ queries.T, axis=1)
            for entry, raw_similarity in zip(entries, similarities):
                similarity = max(0.0, min(1.0, float(raw_similarity)))
                if similarity < self.threshold:
                    continue
                confidence = "높음" if similarity >= 0.90 else "보통" if similarity >= 0.82 else "낮음"
                scored.append(CatalogCandidate(
                    catalog_id=catalog_id, filename=filename,
                    page=int(entry["page"]), image_index=int(entry["image_index"]),
                    similarity=round(similarity, 4), confidence=confidence,
                    note="외형 유사 후보이며 동일 제품·모델로 확정할 수 없습니다.",
                    visual_category=visual_category,
                    visual_features=visual_features,
                    page_excerpt=str(entry.get("page_text") or "") or None,
                ))
        candidates: list[CatalogCandidate] = []
        seen_pages: set[tuple[str, int]] = set()
        for candidate in sorted(scored, key=lambda item: item.similarity, reverse=True):
            page_key = (candidate.catalog_id, candidate.page)
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            candidates.append(candidate)
            if len(candidates) >= limit:
                break
        top_similarity = candidates[0].similarity if candidates else 0.0
        second_similarity = candidates[1].similarity if len(candidates) > 1 else 0.0
        return CatalogMatchSignals(
            candidates=candidates,
            has_visible_text=has_visible_text,
            top_similarity=top_similarity,
            similarity_margin=max(0.0, top_similarity - second_similarity),
            visual_category=visual_category,
            visual_features=tuple(visual_features),
        )

    def match(self, image_path: Path, catalog_ids: list[str], limit: int = 8) -> list[CatalogCandidate]:
        return self.match_with_signals(image_path, catalog_ids, limit).candidates

    def image_path(self, candidate: CatalogCandidate) -> Path | None:
        return self.resolve_image(candidate.catalog_id, candidate.page, candidate.image_index)

    def resolve_image(self, catalog_id: str, page: int, image_index: int) -> Path | None:
        manifest_path = self.root / catalog_id / "manifest.json"
        if not manifest_path.exists():
            return None
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["entries"]:
            if int(entry["page"]) == page and int(entry["image_index"]) == image_index:
                catalog_root = (self.root / catalog_id).resolve()
                candidate = (catalog_root / str(entry["image"])).resolve()
                if candidate == catalog_root or catalog_root not in candidate.parents:
                    return None
                return candidate
        return None
