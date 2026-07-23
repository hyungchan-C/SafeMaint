from pydantic import BaseModel, Field


class CatalogItem(BaseModel):
    manufacturer: str | None = None
    equipment_type: str | None = None
    model_number: str | None = None
    component_name: str | None = None
    description: str | None = None
    specifications: dict[str, str] = Field(default_factory=dict)
    visible_conditions: list[str] = Field(default_factory=list)


class CatalogCandidate(BaseModel):
    catalog_id: str
    filename: str
    page: int
    image_index: int
    similarity: float = Field(ge=0, le=1)
    confidence: str
    note: str
    visual_category: str | None = None
    visual_features: list[str] = Field(default_factory=list)
    page_excerpt: str | None = None


class CatalogAnalysisResponse(BaseModel):
    filename: str
    items: list[CatalogItem]
    extracted_markdown: str | None = None
    table_rows: list[dict[str, str]] = Field(default_factory=list)
    raw_visual_description: str | None = None
    warnings: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    catalog_candidates: list[CatalogCandidate] = Field(default_factory=list)


class CatalogIndexResponse(BaseModel):
    catalog_id: str
    index_version: str = "legacy"
    filename: str
    page_count: int
    image_count: int
    warnings: list[str] = Field(default_factory=list)
