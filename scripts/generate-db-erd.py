from __future__ import annotations

from dataclasses import dataclass
from html import escape
from math import atan2, cos, sin
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = ROOT / "docs" / "safemaint-db-erd.png"
SVG_PATH = ROOT / "docs" / "safemaint-db-erd.svg"

WIDTH = 4400
HEIGHT = 3000

COLORS = {
    "background": "#F4F7FB",
    "foreground": "#172033",
    "muted": "#637083",
    "border": "#C8D2E1",
    "surface": "#FFFFFF",
    "auth": "#6D5BD0",
    "auth_soft": "#EEEAFE",
    "asset": "#2676C8",
    "asset_soft": "#E8F2FC",
    "assessment": "#188476",
    "assessment_soft": "#E4F5F1",
    "rag": "#D06B2C",
    "rag_soft": "#FCEDE3",
    "support": "#667386",
    "support_soft": "#E9EDF3",
    "logical": "#8793A4",
    "pk": "#167A6B",
    "fk": "#674FBE",
    "uq": "#B85E27",
}


def find_font(name: str) -> str:
    candidates = [
        Path("C:/Windows/Fonts") / name,
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError("A readable TrueType font was not found.")


FONT_REGULAR_PATH = find_font("malgun.ttf")
FONT_BOLD_PATH = find_font("malgunbd.ttf")

FONTS = {
    "title": ImageFont.truetype(FONT_BOLD_PATH, 70),
    "subtitle": ImageFont.truetype(FONT_REGULAR_PATH, 31),
    "section": ImageFont.truetype(FONT_BOLD_PATH, 27),
    "table": ImageFont.truetype(FONT_BOLD_PATH, 31),
    "row": ImageFont.truetype(FONT_REGULAR_PATH, 23),
    "row_bold": ImageFont.truetype(FONT_BOLD_PATH, 23),
    "small": ImageFont.truetype(FONT_REGULAR_PATH, 21),
    "small_bold": ImageFont.truetype(FONT_BOLD_PATH, 21),
}


@dataclass(frozen=True)
class TableBox:
    name: str
    subtitle: str
    x: int
    y: int
    w: int
    h: int
    color: str
    soft: str
    rows: tuple[str, ...]

    @property
    def left(self) -> int:
        return self.x

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def top(self) -> int:
        return self.y

    @property
    def bottom(self) -> int:
        return self.y + self.h


TABLES = {
    "user_sites": TableBox(
        "user_sites", "사용자 ↔ 사업장 N:M", 120, 250, 760, 310,
        COLORS["auth"], COLORS["auth_soft"],
        (
            "PK/FK user_id → users.id",
            "PK/FK site_id → sites.id",
            "is_primary · 사용자당 최대 1개",
            "FK assigned_by_user_id → users.id",
            "assigned_at",
        ),
    ),
    "users": TableBox(
        "users", "사원번호 기반 회원", 1150, 170, 940, 520,
        COLORS["auth"], COLORS["auth_soft"],
        (
            "PK id · UUID",
            "UQ employee_number · 로그인 ID",
            "name · email(대소문자 무시 UQ)",
            "department · job_title",
            "auth_provider · local/ldap/oidc",
            "password_hash · 평문 저장 금지",
            "status · active/locked/retired",
            "failed_login_count · locked_until",
            "last_login_at · password_changed_at · deactivated_at",
        ),
    ),
    "user_roles": TableBox(
        "user_roles", "사용자 ↔ 역할 N:M", 2380, 250, 720, 310,
        COLORS["auth"], COLORS["auth_soft"],
        (
            "PK/FK user_id → users.id",
            "PK/FK role_id → roles.id",
            "FK assigned_by_user_id → users.id",
            "assigned_at",
        ),
    ),
    "roles": TableBox(
        "roles", "권한 역할", 3400, 250, 720, 310,
        COLORS["auth"], COLORS["auth_soft"],
        (
            "PK id · UUID",
            "UQ code · worker/safety_manager/admin",
            "name · description",
            "is_active",
        ),
    ),
    "sites": TableBox(
        "sites", "사업장", 120, 800, 760, 350,
        COLORS["asset"], COLORS["asset_soft"],
        (
            "PK id · UUID",
            "UQ code",
            "name",
            "is_active",
            "metadata · JSONB",
        ),
    ),
    "equipment": TableBox(
        "equipment", "설비", 120, 1350, 760, 430,
        COLORS["asset"], COLORS["asset_soft"],
        (
            "PK id · UUID",
            "FK site_id → sites.id",
            "UQ (site_id, code)",
            "name · equipment_type",
            "manufacturer · model_number",
            "is_active",
            "metadata · JSONB",
        ),
    ),
    "components": TableBox(
        "components", "설비 부품", 120, 1970, 760, 430,
        COLORS["asset"], COLORS["asset_soft"],
        (
            "PK id · UUID",
            "FK equipment_id → equipment.id",
            "UQ (equipment_id, code)",
            "name · component_type",
            "manufacturer · part_number",
            "is_active",
            "metadata · JSONB",
        ),
    ),
    "reference_codes": TableBox(
        "reference_codes", "공통 기준 코드", 120, 2570, 760, 300,
        COLORS["support"], COLORS["support_soft"],
        (
            "PK id · UUID",
            "UQ (category, code)",
            "label · description",
            "sort_order · is_active",
        ),
    ),
    "assessments": TableBox(
        "assessments", "위험성평가 본문·입력 스냅샷", 1150, 860, 1050, 650,
        COLORS["assessment"], COLORS["assessment_soft"],
        (
            "PK id · UUID",
            "FK site_id · equipment_id · component_id",
            "FK created_by_user_id · reviewed_by_user_id",
            "site_name · equipment_name · component_name",
            "task_type · energy_sources · description",
            "status · draft/pending_review/approved/rejected",
            "engine_version · rule_version",
            "request_snapshot · JSONB",
            "reviewed_by(레거시) · reviewed_at",
        ),
    ),
    "assessment_hazards": TableBox(
        "assessment_hazards", "평가별 위험요인", 1100, 1730, 780, 440,
        COLORS["assessment"], COLORS["assessment_soft"],
        (
            "PK id · UUID",
            "FK assessment_id → assessments.id",
            "name · accident_type",
            "likelihood(1~4) · severity(1~4)",
            "score(1~16) · risk_level",
            "safety_actions · JSONB",
        ),
    ),
    "checklist_items": TableBox(
        "checklist_items", "TBM 체크리스트", 1970, 1730, 780, 440,
        COLORS["assessment"], COLORS["assessment_soft"],
        (
            "PK id · UUID",
            "FK assessment_id → assessments.id",
            "UQ (assessment_id, sequence)",
            "content · is_completed",
            "FK completed_by_user_id → users.id",
            "completed_by(레거시) · completed_at",
        ),
    ),
    "audit_events": TableBox(
        "audit_events", "변경 이력 · append-only", 1100, 2500, 850, 370,
        COLORS["support"], COLORS["support_soft"],
        (
            "PK id · UUID",
            "event_type",
            "FK actor_user_id → users.id · actor_id(레거시)",
            "entity_type · entity_id",
            "payload · JSONB · created_at",
        ),
    ),
    "documents": TableBox(
        "documents", "검색 원문 메타데이터", 3320, 850, 900, 540,
        COLORS["rag"], COLORS["rag_soft"],
        (
            "PK id · UUID",
            "UQ external_id",
            "title · source_type · publisher",
            "source_url · revision · published_at",
            "access_level · public/restricted/private",
            "UQ file_sha256",
            "metadata · JSONB",
        ),
    ),
    "document_chunks": TableBox(
        "document_chunks", "청크·임베딩", 3320, 1600, 900, 680,
        COLORS["rag"], COLORS["rag_soft"],
        (
            "PK id · UUID",
            "FK document_id → documents.id",
            "UQ (document_id, chunk_index)",
            "page_number · page_start · page_end",
            "section_path · JSONB",
            "content · content_hash",
            "metadata · JSONB",
            "embedding · vector",
            "embedding_model · embedding_dimension",
            "embedding_status · pending/ready/failed/skipped",
        ),
    ),
    "assessment_evidence": TableBox(
        "assessment_evidence", "평가 ↔ 검색 근거", 2460, 2500, 850, 370,
        COLORS["rag"], COLORS["rag_soft"],
        (
            "PK id · UUID",
            "FK assessment_id → assessments.id",
            "FK chunk_id → document_chunks.id",
            "UQ (assessment_id, chunk_id)",
            "retrieval_rank · retrieval_score · reranker_score",
            "used_in_answer",
        ),
    ),
}


@dataclass(frozen=True)
class Edge:
    points: tuple[tuple[int, int], ...]
    color: str
    label: str
    label_at: tuple[int, int]
    dashed: bool = False


EDGES = (
    Edge(((880, 405), (1010, 405), (1010, 420), (1150, 420)), COLORS["auth"], "N:1 사용자", (1015, 360)),
    Edge(((500, 560), (500, 680), (500, 680), (500, 800)), COLORS["auth"], "N:1 사업장", (520, 675)),
    Edge(((2380, 405), (2240, 405), (2240, 430), (2090, 430)), COLORS["auth"], "N:1 사용자", (2240, 360)),
    Edge(((3100, 405), (3240, 405), (3240, 405), (3400, 405)), COLORS["auth"], "N:1 역할", (3240, 360)),
    Edge(((1620, 690), (1620, 770), (1620, 770), (1620, 860)), COLORS["auth"], "작성자·검토자", (1650, 760)),
    Edge(((500, 1150), (500, 1240), (500, 1240), (500, 1350)), COLORS["asset"], "1:N", (530, 1235)),
    Edge(((500, 1780), (500, 1870), (500, 1870), (500, 1970)), COLORS["asset"], "1:N", (530, 1865)),
    Edge(((880, 965), (1010, 965), (1010, 1010), (1150, 1010)), COLORS["asset"], "사업장", (1015, 930)),
    Edge(((880, 1545), (980, 1545), (980, 1160), (1150, 1160)), COLORS["asset"], "설비", (1010, 1495)),
    Edge(((880, 2185), (1040, 2185), (1040, 1320), (1150, 1320)), COLORS["asset"], "부품", (1065, 2135)),
    Edge(((1500, 1510), (1500, 1600), (1490, 1600), (1490, 1730)), COLORS["assessment"], "1:N 위험요인", (1520, 1590)),
    Edge(((1870, 1510), (1870, 1620), (2360, 1620), (2360, 1730)), COLORS["assessment"], "1:N 체크항목", (2110, 1575)),
    Edge(((3770, 1390), (3770, 1490), (3770, 1490), (3770, 1600)), COLORS["rag"], "1:N 청크", (3800, 1480)),
    Edge(((2200, 1320), (2310, 1320), (2310, 2390), (2680, 2390), (2680, 2500)), COLORS["assessment"], "1:N 평가 근거", (2350, 2350)),
    Edge(((3770, 2280), (3770, 2390), (3090, 2390), (3090, 2500)), COLORS["rag"], "1:N 근거 연결", (3450, 2350)),
    Edge(((880, 2720), (980, 2720), (980, 2250), (1490, 2250), (1490, 2170)), COLORS["logical"], "논리 코드 참조", (1090, 2205), True),
    Edge(((2090, 570), (3000, 570), (3000, 2290), (2750, 2290), (2750, 1950)), COLORS["auth"], "체크 완료자", (2810, 2245), True),
    Edge(((2090, 620), (2920, 620), (2920, 2380), (1950, 2380), (1950, 2610)), COLORS["auth"], "감사 행위자", (2030, 2335), True),
)


class Renderer:
    def __init__(self) -> None:
        self.image = Image.new("RGB", (WIDTH, HEIGHT), COLORS["background"])
        self.draw = ImageDraw.Draw(self.image)
        self.svg: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
            "<defs>",
            '<filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="9" stdDeviation="12" flood-color="#172033" flood-opacity="0.12"/></filter>',
            '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker>',
            "</defs>",
            f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{COLORS["background"]}"/>',
        ]

    def text(self, xy: tuple[int, int], value: str, font_key: str, fill: str, anchor: str = "la") -> None:
        font = FONTS[font_key]
        self.draw.text(xy, value, font=font, fill=fill, anchor=anchor)
        size_map = {"title": 70, "subtitle": 31, "section": 27, "table": 31, "row": 23, "row_bold": 23, "small": 21, "small_bold": 21}
        weight = "700" if font_key in {"title", "section", "table", "row_bold", "small_bold"} else "400"
        svg_anchor = {"l": "start", "m": "middle", "r": "end"}.get(anchor[0], "start")
        svg_baseline = "hanging" if len(anchor) > 1 and anchor[1] == "t" else "alphabetic"
        self.svg.append(
            f'<text x="{xy[0]}" y="{xy[1]}" fill="{fill}" font-family="Malgun Gothic, Noto Sans KR, sans-serif" font-size="{size_map[font_key]}" font-weight="{weight}" text-anchor="{svg_anchor}" dominant-baseline="{svg_baseline}">{escape(value)}</text>'
        )

    def rect(self, xy: tuple[int, int, int, int], fill: str, outline: str | None = None, width: int = 1, radius: int = 0, shadow: bool = False) -> None:
        if radius:
            self.draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
        else:
            self.draw.rectangle(xy, fill=fill, outline=outline, width=width)
        x1, y1, x2, y2 = xy
        filter_attr = ' filter="url(#shadow)"' if shadow else ""
        stroke = outline or "none"
        self.svg.append(
            f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"{filter_attr}/>'
        )

    def line(self, points: tuple[tuple[int, int], ...], fill: str, width: int = 5, dashed: bool = False, arrow: bool = True) -> None:
        if dashed:
            for start, end in zip(points, points[1:]):
                self._dashed_segment(start, end, fill, width)
        else:
            self.draw.line(points, fill=fill, width=width, joint="curve")
        if arrow:
            self._arrowhead(points[-2], points[-1], fill, 18)
        dash = ' stroke-dasharray="16 13"' if dashed else ""
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        point_string = " ".join(f"{x},{y}" for x, y in points)
        self.svg.append(
            f'<polyline points="{point_string}" fill="none" stroke="{fill}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"{dash}{marker}/>'
        )

    def _dashed_segment(self, start: tuple[int, int], end: tuple[int, int], fill: str, width: int) -> None:
        x1, y1 = start
        x2, y2 = end
        dx, dy = x2 - x1, y2 - y1
        length = max(abs(dx), abs(dy))
        if length == 0:
            return
        unit_x, unit_y = dx / length, dy / length
        cursor = 0
        while cursor < length:
            segment_end = min(cursor + 16, length)
            self.draw.line(
                (
                    (x1 + unit_x * cursor, y1 + unit_y * cursor),
                    (x1 + unit_x * segment_end, y1 + unit_y * segment_end),
                ),
                fill=fill,
                width=width,
            )
            cursor += 29

    def _arrowhead(self, start: tuple[int, int], end: tuple[int, int], fill: str, size: int) -> None:
        angle = atan2(end[1] - start[1], end[0] - start[0])
        left = (
            end[0] - size * cos(angle - 0.55),
            end[1] - size * sin(angle - 0.55),
        )
        right = (
            end[0] - size * cos(angle + 0.55),
            end[1] - size * sin(angle + 0.55),
        )
        self.draw.polygon((end, left, right), fill=fill)

    def label(self, xy: tuple[int, int], value: str, color: str) -> None:
        bbox = self.draw.textbbox((0, 0), value, font=FONTS["small_bold"])
        w = bbox[2] - bbox[0] + 30
        h = 42
        x, y = xy
        self.rect((x - w // 2, y - h // 2, x + w // 2, y + h // 2), COLORS["surface"], COLORS["border"], 2, 14)
        self.text((x, y + 8), value, "small_bold", color, "ma")

    def draw_table(self, box: TableBox) -> None:
        self.rect((box.left, box.top, box.right, box.bottom), COLORS["surface"], COLORS["border"], 2, 22, shadow=True)
        self.rect((box.left, box.top, box.right, box.top + 76), box.soft, None, 0, 22)
        self.rect((box.left, box.top + 54, box.right, box.top + 76), box.soft)
        self.rect((box.left, box.top, box.left + 12, box.bottom), box.color, None, 0, 10)
        self.text((box.left + 34, box.top + 49), box.name, "table", COLORS["foreground"])
        self.text((box.right - 24, box.top + 47), box.subtitle, "small", COLORS["muted"], "ra")

        y = box.top + 112
        for row in box.rows:
            prefix = row.split(" ", 1)[0]
            if prefix.startswith("PK"):
                key_color = COLORS["pk"]
                font_key = "row_bold"
            elif prefix.startswith("FK"):
                key_color = COLORS["fk"]
                font_key = "row_bold"
            elif prefix.startswith("UQ"):
                key_color = COLORS["uq"]
                font_key = "row_bold"
            else:
                key_color = COLORS["foreground"]
                font_key = "row"
            self.text((box.left + 34, y), row, font_key, key_color)
            y += 40

    def finish(self) -> None:
        self.svg.append("</svg>")
        PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(PNG_PATH, format="PNG", optimize=True)
        SVG_PATH.write_text("\n".join(self.svg), encoding="utf-8")


def main() -> None:
    renderer = Renderer()

    renderer.text((120, 55), "SafeMaint 데이터베이스 구조도", "title", COLORS["foreground"], "lt")
    renderer.text(
        (120, 145),
        "PostgreSQL 16 + pgvector · Alembic 0002_users_roles_sites 코드 기준 · 업무 테이블 15개",
        "subtitle",
        COLORS["muted"],
        "lt",
    )

    section_labels = (
        ((120, 210), "회원·권한", COLORS["auth"]),
        ((120, 760), "사업장·설비", COLORS["asset"]),
        ((1150, 820), "위험성평가", COLORS["assessment"]),
        ((3320, 810), "문서·RAG", COLORS["rag"]),
    )
    for (x, y), label, color in section_labels:
        renderer.text((x, y), label, "section", color, "lt")

    for edge in EDGES:
        renderer.line(edge.points, edge.color, 5, edge.dashed, True)
    for edge in EDGES:
        renderer.label(edge.label_at, edge.label, edge.color)

    for box in TABLES.values():
        renderer.draw_table(box)

    renderer.rect((3520, 2560, 4220, 2870), COLORS["surface"], COLORS["border"], 2, 18)
    renderer.text((3550, 2610), "표기", "section", COLORS["foreground"])
    renderer.text((3550, 2660), "PK  기본키", "small_bold", COLORS["pk"])
    renderer.text((3760, 2660), "FK  외래키", "small_bold", COLORS["fk"])
    renderer.text((3970, 2660), "UQ  고유 제약", "small_bold", COLORS["uq"])
    renderer.text((3550, 2720), "실선  실제 FK 관계", "small", COLORS["foreground"])
    renderer.text((3550, 2770), "점선  사용자 참조 또는 논리 코드 참조", "small", COLORS["muted"])
    renderer.text((3550, 2820), "대부분의 테이블에 created_at / updated_at이 있으며 그림에서는 생략", "small", COLORS["muted"])

    renderer.finish()
    print(PNG_PATH)
    print(SVG_PATH)


if __name__ == "__main__":
    main()
