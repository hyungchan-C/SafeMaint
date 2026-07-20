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
    def __init__(self) -> None:
        self._model = None
        self._transform = None

    def encode(self, image: Image.Image) -> np.ndarray:
        import torch
        from torchvision.models import EfficientNet_B0_Weights, efficientnet_b0

        if self._model is None:
            weights = EfficientNet_B0_Weights.DEFAULT
            model = efficientnet_b0(weights=weights)
            model.classifier = torch.nn.Identity()
            self._model = model.eval().cpu()
            self._transform = weights.transforms()
            torch.set_num_threads(min(4, torch.get_num_threads()))
        assert self._transform is not None
        with torch.inference_mode():
            vector = self._model(self._transform(image.convert("RGB")).unsqueeze(0)).squeeze(0)
        result = vector.float().numpy()
        norm = float(np.linalg.norm(result))
        return result / norm if norm else result


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
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.threshold = threshold
        self.max_pages = max_pages
        self.max_images = max_images
        self.max_image_pixels = max_image_pixels
        self.embedder = _SemanticEmbedder()

    def index_pdf(self, pdf_path: Path, filename: str) -> CatalogIndexResponse:
        data = pdf_path.read_bytes()
        catalog_id = hashlib.sha256(data + b"safemaint-semantic-v2").hexdigest()[:20]
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
                    feature_name = f"{image_name}.npy"
                    np.save(target / feature_name, self.embedder.encode(image), allow_pickle=False)
                    entries.append({
                        "page": page_number, "image_index": image_index,
                        "image": image_name, "feature": feature_name,
                        "page_text": page_text,
                    })
                except Exception:
                    continue
        summary = {
            "catalog_id": catalog_id, "filename": filename,
            "page_count": len(reader.pages), "image_count": len(entries),
            "warnings": warnings,
        }
        manifest_path.write_text(
            json.dumps({"summary": summary, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        return CatalogIndexResponse(**summary)

    def match(self, image_path: Path, catalog_ids: list[str], limit: int = 8) -> list[CatalogCandidate]:
        query_image = Image.open(image_path)
        if query_image.width * query_image.height > self.max_image_pixels:
            raise ValueError(f"이미지 픽셀 수는 {self.max_image_pixels}개를 넘을 수 없습니다.")
        query = self.embedder.encode(query_image)
        scored: list[CatalogCandidate] = []
        for catalog_id in catalog_ids:
            manifest_path = self.root / catalog_id / "manifest.json"
            if not manifest_path.exists():
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            filename = manifest["summary"]["filename"]
            for entry in manifest["entries"][: self.max_images]:
                candidate = np.load(self.root / catalog_id / entry["feature"], allow_pickle=False)
                if candidate.shape != query.shape:
                    # Ignore indexes made by an older descriptor version. Re-uploading the
                    # catalog creates the current semantic index without breaking analysis.
                    continue
                similarity = max(0.0, min(1.0, float(np.dot(query, candidate))))
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
