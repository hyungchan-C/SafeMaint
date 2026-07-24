from __future__ import annotations

from collections.abc import Iterable


OFFICIAL_DOCUMENT_TYPES = frozenset(
    {
        "public_law",
        "public_guide",
        "public_incident",
        "public_media",
        "company_policy",
        "equipment_manual",
        "component_manual",
    }
)

LEGACY_DOCUMENT_TYPE_ALIASES = {
    "regulation": "public_law",
    "law": "public_law",
    "guide": "public_guide",
    "incident": "public_incident",
    "manual": "equipment_manual",
    "work_standard": "company_policy",
}

MANUAL_DOCUMENT_TYPES = frozenset({"equipment_manual", "component_manual"})
COMPONENT_DOCUMENT_TYPES = (
    "component_manual",
    "equipment_manual",
    "public_guide",
    "public_law",
)
MAINTENANCE_DOCUMENT_TYPES = (
    "component_manual",
    "equipment_manual",
    "company_policy",
    "public_law",
    "public_guide",
    "public_incident",
)


def canonical_document_type(value: str | None) -> str:
    normalized = (value or "").strip().casefold()
    return LEGACY_DOCUMENT_TYPE_ALIASES.get(normalized, normalized)


def canonical_document_types(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            canonical
            for value in values
            if (canonical := canonical_document_type(value))
        )
    )
