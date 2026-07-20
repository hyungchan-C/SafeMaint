from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from vision_service.schemas import CatalogCandidate, CatalogIndexResponse


class _SemanticEmbedder:
    def __init__(self, model_name: str, device: str, cache_dir: str) -> None:
        self.model_name = model_name
        self.device = device
        self.cache_dir = cache_dir
        self._model = None
        self._processor = None

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
        max_pages: int = 500,
        max_images: int = 5000,
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

    def index_pdf(self, pdf_path: Path, filename: str) -> CatalogIndexResponse:
        data = pdf_path.read_bytes()
        index_version = f"safemaint-matrix-v4:{self.embedding_model}".encode()
        catalog_id = hashlib.sha256(data + index_version).hexdigest()[:20]
        target = self.root / catalog_id
        manifest_path = target / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        for page_number, page in enumerate(reader.pages, start=1):
            if len(entries) >= self.max_images:
                warnings.append(f"이미지는 최대 {self.max_images}개까지만 인덱싱했습니다.")
                break
            page_text = " ".join((page.extract_text() or "").split())[:1600]
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
                    image_name = f"p{page_number:04d}-i{image_index:03d}.jpg"
                    image.save(target / image_name, format="JPEG", quality=90)
                    vector_index = len(embeddings)
                    embeddings.append(self.embedder.encode(image))
                    entries.append({
                        "page": page_number, "image_index": image_index,
                        "image": image_name, "vector_index": vector_index,
                        "page_text": page_text,
                    })
                except Exception:
                    continue
        summary = {
            "catalog_id": catalog_id, "filename": filename,
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

    def match(self, image_path: Path, catalog_ids: list[str], limit: int = 8) -> list[CatalogCandidate]:
        query_image = Image.open(image_path)
        if query_image.width * query_image.height > self.max_image_pixels:
            raise ValueError(f"이미지 픽셀 수는 {self.max_image_pixels}개를 넘을 수 없습니다.")
        queries = self.embedder.encode_many(self._query_views(query_image))
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
                    page_excerpt=str(entry.get("page_text") or "") or None,
                ))
        return sorted(scored, key=lambda item: item.similarity, reverse=True)[:limit]

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
