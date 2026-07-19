from collections.abc import Sequence
from typing import TypedDict

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentType,
    Permission,
    ReferenceCode,
    Role,
    RolePermission,
)
from app.db.session import SessionLocal


class ReferenceCodeSeed(TypedDict):
    category: str
    code: str
    label: str
    sort_order: int


class RoleSeed(TypedDict):
    code: str
    name: str
    description: str


class PermissionSeed(TypedDict):
    code: str
    name: str
    description: str


class DocumentTypeSeed(TypedDict):
    code: str
    name: str
    scope: str
    is_exportable: bool


REFERENCE_CODES: Sequence[ReferenceCodeSeed] = (
    {"category": "assessment_status", "code": "draft", "label": "초안", "sort_order": 10},
    {"category": "assessment_status", "code": "pending_review", "label": "검토 대기", "sort_order": 20},
    {"category": "assessment_status", "code": "approved", "label": "승인", "sort_order": 30},
    {"category": "assessment_status", "code": "rejected", "label": "반려", "sort_order": 40},
    {"category": "risk_level", "code": "low", "label": "낮음", "sort_order": 10},
    {"category": "risk_level", "code": "medium", "label": "보통", "sort_order": 20},
    {"category": "risk_level", "code": "high", "label": "높음", "sort_order": 30},
    {"category": "accident_type", "code": "pinch", "label": "끼임", "sort_order": 10},
    {"category": "accident_type", "code": "electric_shock", "label": "감전", "sort_order": 20},
    {"category": "accident_type", "code": "fall", "label": "추락", "sort_order": 30},
    {"category": "accident_type", "code": "falling_object", "label": "낙하물", "sort_order": 40},
    {"category": "accident_type", "code": "collision", "label": "충돌", "sort_order": 50},
    {"category": "accident_type", "code": "fire_explosion", "label": "화재·폭발", "sort_order": 60},
    {"category": "task_type", "code": "inspection", "label": "점검", "sort_order": 10},
    {"category": "task_type", "code": "cleaning", "label": "청소", "sort_order": 20},
    {"category": "task_type", "code": "jam_removal", "label": "이물질 제거", "sort_order": 30},
    {"category": "task_type", "code": "replacement", "label": "부품 교체", "sort_order": 40},
    {"category": "task_type", "code": "repair", "label": "수리", "sort_order": 50},
    {"category": "energy_source", "code": "electric", "label": "전기", "sort_order": 10},
    {"category": "energy_source", "code": "hydraulic", "label": "유압", "sort_order": 20},
    {"category": "energy_source", "code": "pneumatic", "label": "공압", "sort_order": 30},
    {"category": "energy_source", "code": "mechanical", "label": "기계", "sort_order": 40},
    {"category": "energy_source", "code": "thermal", "label": "열", "sort_order": 50},
    {"category": "energy_source", "code": "chemical", "label": "화학", "sort_order": 60},
    {"category": "energy_source", "code": "gravity", "label": "중력", "sort_order": 70},
)


ROLES: Sequence[RoleSeed] = (
    {
        "code": "worker",
        "name": "작업자",
        "description": "본인에게 허용된 사업장의 작업 및 점검 정보를 사용합니다.",
    },
    {
        "code": "safety_manager",
        "name": "안전관리자",
        "description": "위험성평가를 검토하고 안전조치 이행을 관리합니다.",
    },
    {
        "code": "document_manager",
        "name": "Document manager",
        "description": "Approves, deletes, restores, and manages document versions.",
    },
    {
        "code": "admin",
        "name": "시스템 관리자",
        "description": "사용자, 역할 및 사업장 접근 권한을 관리합니다.",
    },
)


PERMISSIONS: Sequence[PermissionSeed] = tuple(
    {
        "code": code,
        "name": name,
        "description": description,
    }
    for code, name, description in (
        ("document.read", "Read documents", "Read active accessible documents."),
        ("document.upload", "Upload documents", "Upload company documents."),
        ("document.update", "Update documents", "Update document metadata."),
        ("document.replace", "Replace documents", "Create replacement versions."),
        ("document.delete", "Delete documents", "Soft-delete documents."),
        ("document.approve", "Approve documents", "Activate reviewed versions."),
        ("document.restore", "Restore documents", "Restore soft-deleted documents."),
        ("role.manage", "Manage roles", "Manage user roles and permissions."),
        ("audit.read", "Read audit logs", "Read audit events."),
        (
            "public_package.import",
            "Import public packages",
            "Import signed public RAG packages.",
        ),
    )
)

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "worker": ("document.read",),
    "safety_manager": (
        "document.read",
        "document.upload",
        "document.update",
        "document.replace",
    ),
    "document_manager": (
        "document.read",
        "document.upload",
        "document.update",
        "document.replace",
        "document.delete",
        "document.approve",
        "document.restore",
        "audit.read",
    ),
    "admin": tuple(item["code"] for item in PERMISSIONS),
}

DOCUMENT_TYPES: Sequence[DocumentTypeSeed] = (
    {"code": "public_incident", "name": "Public incident", "scope": "public", "is_exportable": True},
    {"code": "public_law", "name": "Public law", "scope": "public", "is_exportable": True},
    {"code": "public_guide", "name": "Public safety guide", "scope": "public", "is_exportable": True},
    {"code": "public_media", "name": "Public safety media", "scope": "public", "is_exportable": True},
    {"code": "company_policy", "name": "Company policy", "scope": "company", "is_exportable": False},
    {"code": "equipment_manual", "name": "Equipment manual", "scope": "company", "is_exportable": False},
    {"code": "component_manual", "name": "Component manual", "scope": "company", "is_exportable": False},
    {"code": "legacy_document", "name": "Legacy document", "scope": "company", "is_exportable": False},
)


def seed_reference_data(session: Session) -> int:
    statement = insert(ReferenceCode).values(list(REFERENCE_CODES))
    statement = statement.on_conflict_do_update(
        constraint="uq_reference_codes_category_code",
        set_={
            "label": statement.excluded.label,
            "sort_order": statement.excluded.sort_order,
            "is_active": True,
            "updated_at": func.now(),
        },
    )
    try:
        result = session.execute(statement)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return max(result.rowcount or 0, 0)


def seed_roles(session: Session) -> int:
    statement = insert(Role).values(list(ROLES))
    statement = statement.on_conflict_do_update(
        constraint="uq_roles_code",
        set_={
            "name": statement.excluded.name,
            "description": statement.excluded.description,
            "is_active": True,
            "updated_at": func.now(),
        },
    )
    try:
        result = session.execute(statement)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return max(result.rowcount or 0, 0)


def seed_security_and_document_types(session: Session) -> tuple[int, int]:
    permission_statement = insert(Permission).values(list(PERMISSIONS))
    permission_statement = permission_statement.on_conflict_do_update(
        constraint="uq_permissions_code",
        set_={
            "name": permission_statement.excluded.name,
            "description": permission_statement.excluded.description,
            "is_active": True,
            "updated_at": func.now(),
        },
    )
    document_type_statement = insert(DocumentType).values(list(DOCUMENT_TYPES))
    document_type_statement = document_type_statement.on_conflict_do_update(
        constraint="uq_document_types_code",
        set_={
            "name": document_type_statement.excluded.name,
            "scope": document_type_statement.excluded.scope,
            "is_exportable": document_type_statement.excluded.is_exportable,
            "is_active": True,
            "updated_at": func.now(),
        },
    )
    try:
        permission_result = session.execute(permission_statement)
        document_type_result = session.execute(document_type_statement)
        role_ids = dict(session.execute(select(Role.code, Role.id)).all())
        permission_ids = dict(
            session.execute(select(Permission.code, Permission.id)).all()
        )
        assignments = [
            {
                "role_id": role_ids[role_code],
                "permission_id": permission_ids[permission_code],
            }
            for role_code, permission_codes in ROLE_PERMISSIONS.items()
            for permission_code in permission_codes
            if role_code in role_ids and permission_code in permission_ids
        ]
        if assignments:
            session.execute(
                insert(RolePermission).values(assignments).on_conflict_do_nothing()
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    return (
        max(permission_result.rowcount or 0, 0),
        max(document_type_result.rowcount or 0, 0),
    )


def main() -> None:
    with SessionLocal() as session:
        reference_rows = seed_reference_data(session)
        role_rows = seed_roles(session)
        permission_rows, document_type_rows = seed_security_and_document_types(session)
    print(
        "SafeMaint seed completed: "
        f"{reference_rows} reference rows, {role_rows} role rows, "
        f"{permission_rows} permission rows, and {document_type_rows} "
        "document type rows applied"
    )


if __name__ == "__main__":
    main()
