from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.db.models import PublicRagPackage
from app.db.session import SessionLocal
from app.services.public_packages import (
    create_public_package,
    import_public_package,
    rollback_public_package,
    verify_public_package,
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Manage signed SafeMaint public RAG packages.")
    commands = value.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--private-key", required=True)
    create.add_argument("--previous-version")
    verify = commands.add_parser("verify")
    verify.add_argument("package", type=Path)
    verify.add_argument("--public-key", required=True)
    install = commands.add_parser("import")
    install.add_argument("package", type=Path)
    install.add_argument("--public-key", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--version", required=True)
    commands.add_parser("status")
    return value


def main() -> None:
    args = parser().parse_args()
    with SessionLocal() as session:
        if args.command == "create":
            result = create_public_package(
                session,
                args.output,
                package_version=args.version,
                private_key=args.private_key,
                previous_version=args.previous_version,
            )
        elif args.command == "verify":
            result = verify_public_package(
                args.package,
                args.public_key,
                expected_embedding_model=settings.rag_model,
                expected_embedding_dimension=settings.rag_embedding_dimension,
            ).manifest
        elif args.command == "import":
            verified = verify_public_package(
                args.package,
                args.public_key,
                expected_embedding_model=settings.rag_model,
                expected_embedding_dimension=settings.rag_embedding_dimension,
            )
            package = import_public_package(session, verified)
            result = {"package_version": package.package_version, "status": package.status}
        elif args.command == "rollback":
            package = rollback_public_package(session, args.version)
            result = {"package_version": package.package_version, "status": package.status}
        else:
            result = [
                {"package_version": row.package_version, "status": row.status}
                for row in session.scalars(
                    select(PublicRagPackage).order_by(PublicRagPackage.created_at.desc())
                )
            ]
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
