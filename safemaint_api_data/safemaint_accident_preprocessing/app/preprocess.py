from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import unescape
import re
from typing import Any

from bs4 import BeautifulSoup

from app.admin_areas import (
    CANONICAL_PROVINCES,
    CITY_DISTRICTS,
    PROVINCE_ALIASES,
    PROVINCE_SIGUNGU,
    SIGUNGU_TO_PROVINCES,
)

NULL_LITERALS = {"", "null", "none", "nan"}
WHITESPACE_RE = re.compile(r"[^\S\r\n]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
FATAL_TITLE_RE = re.compile(
    r"^\s*\[\s*(?P<date>[^,\]]+?)\s*,\s*(?P<location>[^\]]+?)\s*\]\s*(?P<title>.*)\s*$"
)
FATAL_JOINED_GYEONGGI_GWANGJU_RE = re.compile(r"^경기\s*광주(?:시)?$")

# 기존 국내재해 업종·기인물 로직은 이 목록과 추출 함수를 그대로 유지한다.
DOMESTIC_FIELD_LABELS = [
    "제목", "날짜", "업종", "기인물", "피해정도", "공정", "재해유형", "사고유형",
    "공사금액", "재해내용요약", "재해개요", "발생개요", "작업상황", "재해발생현황",
    "재해원인", "발생원인", "사고원인", "원인", "대책", "예방대책", "재해예방대책",
    "동종재해예방대책", "안전대책", "재발방지대책", "발생일시", "발생월일", "소재지",
    "시공사", "공사명", "피재자", "공사규모",
]


def _spaced_label_pattern(label: str) -> str:
    compact = re.sub(r"\s+", "", label)
    return r"\s*".join(re.escape(character) for character in compact)


DOMESTIC_LABEL_PATTERN = "|".join(
    sorted((_spaced_label_pattern(label) for label in DOMESTIC_FIELD_LABELS), key=len, reverse=True)
)
DOMESTIC_LABEL_RE = re.compile(
    rf"(?:【\s*)?(?P<label>{DOMESTIC_LABEL_PATTERN})(?:\s*】)?\s*[:：]",
    re.IGNORECASE,
)

FULL_DATE_PATTERNS = [
    re.compile(
        r"(?<!\d)2(?P<year>20\d{2})\s*(?:[./-]\s*)?(?P<month>\d{1,2})"
        r"\s*(?:[./-]\s*)?(?P<day>\d{1,2})(?:\s*일)?"
    ),
    re.compile(
        r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*년\s*(?P<month>\d{1,2})"
        r"\s*월\s*(?P<day>\d{1,2})\s*일"
    ),
    re.compile(
        r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*(?:[./-]\s*)?(?P<month>\d{1,2})"
        r"\s*(?:[./-]\s*)?(?P<day>\d{1,2})(?:\s*일)?"
    ),
]
DOMESTIC_FULL_DATE_PATTERNS = [
    re.compile(
        r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*년\s*(?P<month>\d{1,2})"
        r"\s*월\s*(?P<day>\d{1,2})\s*일"
    ),
    re.compile(
        r"(?<!\d)(?P<year>(?:19|20)\d{2})\s*[./-]\s*(?P<month>\d{1,2})"
        r"\s*[./-]\s*(?P<day>\d{1,2})(?!\d)"
    ),
]
DOMESTIC_DATE_LABELS = [
    "발생일시", "발생월일", "사고일시", "사고일자", "사고발생일시",
    "사고발생일", "재해발생일시", "재해발생일", "날짜",
]
DOMESTIC_DATE_LABEL_PATTERN = "|".join(
    sorted((_spaced_label_pattern(label) for label in DOMESTIC_DATE_LABELS), key=len, reverse=True)
)
DOMESTIC_DATE_LABEL_RE = re.compile(
    rf"(?:【\s*)?(?P<label>{DOMESTIC_DATE_LABEL_PATTERN})(?:\s*】)?\s*[:：]",
    re.IGNORECASE,
)
DOMESTIC_NON_ACCIDENT_TITLE_RE = re.compile(
    r"(?:세미나|간담회|교육|행사|공지|안내|자료|압축파일|사례모음|통계|목록|다운로드)",
    re.IGNORECASE,
)
DOMESTIC_NON_ACCIDENT_DATE_CONTEXT_RE = re.compile(
    r"(?:등록일|설치일|설치년도|제조일|작성일|게시일|개최일|행사일|세미나|간담회|"
    r"자료\s*다운로드|repository|타임스탬프|속보\s*작성)",
    re.IGNORECASE,
)
DOMESTIC_ACCIDENT_DATE_CONTEXT_RE = re.compile(
    r"(?:사고|재해|발생|사망|부상|추락|협착|감전|붕괴|폭발|화재|매몰|깔림|충돌|끼임|"
    r"작업\s*중|\d{1,2}:\d{2}\s*(?:분)?경)",
    re.IGNORECASE,
)
TITLE_MONTH_DAY_RE = re.compile(r"^\s*(?P<month>\d{1,2})\s*[./-]\s*(?P<day>\d{1,2})\s*$")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

PROVINCE_TOKEN_ALT = "|".join(
    sorted((re.escape(value) for value in PROVINCE_ALIASES), key=len, reverse=True)
)
ADMIN_TOKEN = (
    r"(?:[가-힣]{1,16}\d+가|[가-힣0-9]{1,16}(?:시|군|구|읍|면|동|리))"
    r"(?=$|\s|\d|[,.;:：()\[\]{}■◆●▶·-]|에서|에는|으로|까지|부터|에|소재|내)"
)
PROVINCE_CANDIDATE_RE = re.compile(
    rf"(?<![가-힣])(?P<location>(?:{PROVINCE_TOKEN_ALT})(?:\s+{ADMIN_TOKEN}){{0,4}})"
)
CITY_CANDIDATE_RE = re.compile(
    rf"(?<![가-힣])(?P<location>[가-힣]{{2,12}}(?:시|군)(?:\s+{ADMIN_TOKEN}){{0,3}})"
)
LOCATION_EVIDENCE_RE = re.compile(
    r"(?:에\s*)?(?:소재(?:한|의)?|내(?:에서)?|현장|사업장|공장|축사|작업장|항만|아파트|창고|공사)"
)
PLACEHOLDER_RE = re.compile(r"(?:○○|OO|XX|000+)", re.IGNORECASE)

DOMESTIC_LOCATION_LABELS = ["소재지", "발생장소", "사고장소", "재해발생장소", "재해장소", "지역"]
DOMESTIC_LOCATION_LABEL_PATTERN = "|".join(
    sorted((_spaced_label_pattern(label) for label in DOMESTIC_LOCATION_LABELS), key=len, reverse=True)
)
DOMESTIC_LOCATION_LABEL_RE = re.compile(
    rf"(?:【\s*)?(?P<label>{DOMESTIC_LOCATION_LABEL_PATTERN})(?:\s*】)?\s*[:：]",
    re.IGNORECASE,
)
DOMESTIC_ALL_METADATA_LABELS = list(dict.fromkeys(DOMESTIC_FIELD_LABELS + DOMESTIC_LOCATION_LABELS))
DOMESTIC_ALL_METADATA_PATTERN = "|".join(
    sorted((_spaced_label_pattern(label) for label in DOMESTIC_ALL_METADATA_LABELS), key=len, reverse=True)
)
DOMESTIC_ALL_METADATA_RE = re.compile(
    rf"(?:【\s*)?(?P<label>{DOMESTIC_ALL_METADATA_PATTERN})(?:\s*】)?\s*[:：]",
    re.IGNORECASE,
)
DOMESTIC_LOCATION_MARKER_RE = re.compile(r"\s*[■◆●▶]\s*|\s+\d+\.\s*(?:재해|작업|원인|대책|예방|상황)")

STRICT_ADMIN_TOKEN = (
    r"(?:[가-힣0-9]{1,16}(?:시|군|구|읍|면|동|리)|[가-힣]{1,16}\d+가)"
    r"(?![가-힣0-9])"
)
STRICT_PROVINCE_RE = re.compile(
    rf"(?<![가-힣])(?P<location>(?:{PROVINCE_TOKEN_ALT})(?:\s+{STRICT_ADMIN_TOKEN}){{0,4}})"
)
STRICT_CITY_RE = re.compile(
    rf"(?<![가-힣])(?P<location>[가-힣]{{2,12}}(?:시|군)(?:\s+{STRICT_ADMIN_TOKEN}){{0,3}})"
)
STRICT_SIGUNGU_STEMS = sorted(
    {name[:-1] for name in SIGUNGU_TO_PROVINCES if len(name[:-1]) >= 2},
    key=len,
    reverse=True,
)
STRICT_SIGUNGU_STEM_ALT = "|".join(re.escape(value) for value in STRICT_SIGUNGU_STEMS)
STRICT_CITY_STEM_RE = re.compile(
    rf"(?<![가-힣])(?P<location>(?:{STRICT_SIGUNGU_STEM_ALT})(?:\s+{STRICT_ADMIN_TOKEN}){{0,3}})(?![가-힣0-9])"
)
STRICT_LOCATION_PATTERNS = (STRICT_PROVINCE_RE, STRICT_CITY_RE, STRICT_CITY_STEM_RE)
LOCATION_ROUTE_RE = re.compile(
    r"(?:[가-힣0-9]{2,16}(?:시|군|구))"
    r"(?:\s+[가-힣0-9]{1,16}(?:읍|면|동|리)){0,2}\s*(?:에서|부터)"
    r".{0,80}?(?:[가-힣0-9]{2,16}(?:시|군|구))"
    r"(?:\s+[가-힣0-9]{1,16}(?:읍|면|동|리)){0,2}\s*(?:까지|간)"
    r".{0,60}?(?:도로|공사|구간|현장)"
)
LOCATION_TIME_PROVINCE_RE = re.compile(
    rf"(\d{{1,2}}:\d{{2}}\s*경)(?=(?:{PROVINCE_TOKEN_ALT}))"
)
LOCATION_AFFILIATION_RE = re.compile(
    r"(?:재해자|피재자|작업자|근로자)?\s*소속\s*업체(?:는|가|의)?\s*$"
    r"|(?:소속업체|본사|사업주\s*주소|업체\s*주소)(?:는|가|의)?\s*$"
)
LOCATION_AFFILIATION_AFTER_RE = re.compile(
    r"^\s*(?:에\s*)?소재(?:한|의)?\s*.{0,80}?"
    r"(?:소속\s*(?:재해자|피재자|작업자|근로자)|(?:공장|업체)\s*소속)"
)
LOCATION_SITE_RE = re.compile(
    r"^\s*(?:에\s*)?(?:소재(?:한|의)?\s*)?.{0,60}?"
    r"(?:공사현장|사업장|작업장|주차장|아파트|공장|현장|축사|창고|항만|조선소|리조트)"
    r"(?:\s*내|내|에서|에|의|\s|$)"
)
LOCATION_PLACEHOLDER_DETAIL_RE = re.compile(r"(?:00|OO|○○|XX|X{2,}|O{2,})", re.I)
NON_ADMIN_DETAIL_TOKENS = {
    "울타리", "석면", "폐기물처리", "폐기물", "처리", "수리",
    "공동", "자동", "운동", "철근콘크리", "수성구민운동",
}
FALSE_DETAIL_CONTINUATION_RE = re.compile(
    r"(?:초등학교|학교|공단|조선소|리조트|운동장|주택|제품|처리업|제조업|"
    r"자동차|콘크리트|해체|설치|공사|업)"
)


@dataclass(frozen=True)
class LocationParts:
    location: str | None
    sido: str | None
    sigungu: str | None
    detail: str | None
    valid: bool
    raw: str | None = None

    @property
    def specificity(self) -> int:
        score = 1 if self.sido else 0
        score += len(self.sigungu.split()) if self.sigungu else 0
        score += len(self.detail.split()) if self.detail else 0
        return score


def normalize_nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in NULL_LITERALS else text


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unescape(str(value)).replace("\r\n", "\n").replace("\r", "\n")
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    return MULTI_NEWLINE_RE.sub("\n\n", "\n".join(lines)).strip()


def _html_to_lines(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for tag in soup.find_all("img"):
        tag.decompose()
    for tag in soup.find_all(["br", "p", "div", "li", "tr", "section", "article", "pre"]):
        if tag.name == "br":
            tag.replace_with("\n")
        else:
            tag.insert_before("\n")
            tag.insert_after("\n")
    return soup.get_text(separator="")


# 띄어쓰기가 붙었다고 확실히 판단할 수 있는 경계만 복구한다.
# 일반 한글 형태소 분리는 하지 않으며, 아래의 제한된 문맥만 처리한다.
SAFE_GLUED_BOUNDARY_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # 조사 `에서` 뒤에 주어가 붙은 경우: 공정에서재해자가 → 공정에서 재해자가
    (
        "locative_subject",
        re.compile(r"(에서)(?=(?:재해자|작업자|근로자))"),
        r"\1 ",
    ),
    # 현장·사업장 등 위치 표현 뒤에 장비/작업명이 붙은 경우
    (
        "site_particle",
        re.compile(
            r"((?:작업장|사업장|공사현장|벌목현장|산림\s*방제\s*현장|현장|축사|공장|"
            r"처리장|창고|야적장|항만|계선주)에서)"
            r"(?=(?:지게차|엘리베이터|천장크레인|벌목|차량탑재형|재해자|작업자|"
            r"근로자|크레인|굴착기|고소작업대|벽체|파일|철골|자재|차량|판넬|H빔))"
        ),
        r"\1 ",
    ),
    # 완결된 주어 뒤에 다음 문장성분이 붙은 경우
    (
        "subject_boundary",
        re.compile(r"((?:재해자|작업자|근로자)가)(?=[가-힣])"),
        r"\1 ",
    ),
    # `작업 중인/중이던/중에/중으로/중지/중단` 등 정상 결합은 제외한다.
    (
        "during_work",
        re.compile(
            r"(작업\s*중)(?=(?!(?:이|인|에|의|으|지|단|임|량|독))[가-힣])"
        ),
        r"\1 ",
    ),
    # `하던 중이던/중에/중으로` 등은 유지하고, 뒤에 새 문장성분이 붙은 경우만 분리한다.
    (
        "while_action",
        re.compile(
            r"(하던\s*중)(?=(?!(?:이|인|에|의|으|지|단|임))[가-힣])"
        ),
        r"\1 ",
    ),
    (
        "fall_result",
        re.compile(r"(넘어져)(?=(?!(?:서|도))[가-힣])"),
        r"\1 ",
    ),
    (
        "transport_modifier",
        re.compile(r"(운반하던)(?=(?:중|입식|좌식|전동|수동|지게차|자재|H빔|보일러|철선|코일|곡물))"),
        r"\1 ",
    ),
    (
        "purpose_boundary",
        re.compile(
            r"((?:이동하기|진입하기|접근하기)\s*위해)"
            r"(?=(?:엘리베이터|고소작업대|사다리|작업대|계단|통로))"
        ),
        r"\1 ",
    ),
    (
        "after_connector",
        re.compile(r"((?:탑승하여|접촉하여|부딪혀))(?=(?!(?:서|야)|도(?:\s|$|[.,;:]))[가-힣])"),
        r"\1 ",
    ),
    (
        "after_collision",
        re.compile(r"(부딪힌\s*후)(?=(?:접안시설|벽체|차량|설비|구조물))"),
        r"\1 ",
    ),
    (
        "pushed_body",
        re.compile(r"(밀리자)(?=몸으로)"),
        r"\1 ",
    ),
    (
        "while_riding",
        re.compile(r"(채로)(?=후진)"),
        r"\1 ",
    ),
    (
        "forklift_subject",
        re.compile(r"(지게차가)(?=넘어져)"),
        r"\1 ",
    ),
    (
        "beam_chain",
        re.compile(r"(H빔이)(?=연쇄적으로)"),
        r"\1 ",
    ),
    (
        "pipe_subject",
        re.compile(r"(강관이)(?=재해자)"),
        r"\1 ",
    ),
    ("fall_then_falling", re.compile(r"(떨어져)(?=낙하하는)"), r"\1 "),
    # 결과 연결어 뒤에 재해자·작업자·근로자가 붙은 확정 사례
    ("fall_then_subject", re.compile(r"(떨어져)(?=(?:재해자|작업자|근로자))"), r"\1 "),
    # 원인 연결어 뒤에 인양 작업이 붙은 확정 사례
    ("cause_then_lifting", re.compile(r"(인해)(?=인양)"), r"\1 "),
    ("during_adjacent", re.compile(r"(작업\s*중)(?=인접한)"), r"\1 "),
    ("climb_panel", re.compile(r"(올라가)(?=(?:판넬|고압전선))"), r"\1 "),
    ("roller_collision", re.compile(r"(롤러에)(?=부딪혀)"), r"\1 "),
    (
        "site_equipment_extended",
        re.compile(
            r"((?:공사현장|사업장|벌목현장|야적장|물류센터|센터)에서)"
            r"(?=(?:윈치|섬유로프|다른|도보|배수관|화물차량|반응기|적재물|이동|타워크레인))"
        ),
        r"\1 ",
    ),
    ("connector_noun_extended", re.compile(r"((?:연결하여|못하여|충격하여))(?=(?:사상|화물차량|중심))"), r"\1 "),
    ("during_lifting_hook", re.compile(r"(작업\s*중)(?=인양고리)"), r"\1 "),
    (
        "compact_domestic_accident_header",
        re.compile(
            rf"(?P<weekday>\([월화수목금토일]\))"
            rf"(?P<time>\d{{1,2}}:\d{{2}}경)"
            rf"(?P<province>{PROVINCE_TOKEN_ALT})\s+"
            rf"(?P<city>[가-힣]{{2,12}}(?:시|군|구))\s+소재"
            rf"(?P<mask>(?:O{{2,}}|0{{2,}}|○{{2,}}|X{{2,}}))"
            rf"(?P<site>공사현장)(?P<floor>\d+층)"
            rf"(?P<place>[가-힣]{{1,12}}에서)(?P<work>[가-힣]{{2,20}}공사)",
            re.IGNORECASE,
        ),
        r"\g<weekday> \g<time> \g<province> \g<city> 소재 \g<mask> \g<site> \g<floor> \g<place> \g<work>",
    ),
)


def find_known_glued_boundaries(text: str | None) -> list[str]:
    """아직 남아 있는 명확한 붙어쓰기 규칙 이름을 반환한다."""
    if not text:
        return []
    return [
        rule_name
        for rule_name, pattern, _replacement in SAFE_GLUED_BOUNDARY_RULES
        if pattern.search(text)
    ]


def repair_safe_glued_boundaries(text: str) -> str:
    """명확한 사고서술 경계만 공백으로 복구한다.

    위치·날짜·업종·기인물 추출 규칙은 건드리지 않는다. 일반 형태소 분석이나
    임의 문장 재작성은 하지 않고 SAFE_GLUED_BOUNDARY_RULES에 등록된 경우만 바꾼다.
    """
    result = text
    for _rule_name, pattern, replacement in SAFE_GLUED_BOUNDARY_RULES:
        result = pattern.sub(replacement, result)
    return result


def html_to_text(raw_value: Any) -> str | None:
    raw = normalize_nullable_text(raw_value)
    if raw is None:
        return None
    text = _html_to_lines(raw) if re.search(r"<[^>]+>", raw) else raw
    cleaned = repair_safe_glued_boundaries(normalize_text(text))
    return cleaned or None


def _compact_label(label: str) -> str:
    return re.sub(r"\s+", "", label).lower()


def extract_labeled_value(text: str | None, target_label: str) -> str | None:
    """기존 국내재해 업종·기인물 추출 규칙."""
    if not text:
        return None
    matches = list(DOMESTIC_LABEL_RE.finditer(text))
    target = _compact_label(target_label)

    for index, match in enumerate(matches):
        if _compact_label(match.group("label")) != target:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = re.sub(r"\s+", " ", text[start:end]).strip(" \n\t:：,")
        if not value:
            return None
        if target == _compact_label("업종"):
            value = re.split(r"\s*[;；]\s*", value, maxsplit=1)[0].strip()
        if target == _compact_label("기인물"):
            value = value.lstrip("-–—:：; ").strip()
            value = re.split(r"\s+-\s+|\s*[;；]\s*", value, maxsplit=1)[0].strip()
            if len(value) > 30:
                topic_match = re.match(r"^(.{1,60}?)(?:은|는)\s", value)
                if topic_match:
                    value = topic_match.group(1).strip()
        return value or None
    return None


def _extract_iso_date(text: str | None, patterns: list[re.Pattern[str]], limit: int) -> str | None:
    if not text:
        return None
    candidates: list[tuple[int, int, str]] = []
    for priority, pattern in enumerate(patterns):
        for match in pattern.finditer(text[:limit]):
            try:
                parsed = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
            except ValueError:
                continue
            candidates.append((match.start(), priority, parsed.isoformat()))
    return min(candidates)[2] if candidates else None


def _iter_valid_date_matches(
    text: str,
    patterns: list[re.Pattern[str]],
    *,
    limit: int,
) -> list[tuple[int, int, date, re.Match[str]]]:
    rows: list[tuple[int, int, date, re.Match[str]]] = []
    for priority, pattern in enumerate(patterns):
        for match in pattern.finditer(text[:limit]):
            try:
                parsed = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
            except ValueError:
                continue
            rows.append((match.start(), priority, parsed, match))
    rows.sort(key=lambda row: (row[0], row[1]))
    return rows


def _domestic_date_candidate_is_accident(
    search_area: str,
    start: int,
    match: re.Match[str],
) -> bool:
    before = search_area[max(0, start - 100):start]
    after = search_area[match.end():match.end() + 280]
    context = f"{before} {after}"
    if DOMESTIC_NON_ACCIDENT_DATE_CONTEXT_RE.search(context):
        return False
    if re.search(r"(?:조사\s*속보|대산업사고조사속보|발행일|문서번호)", context, re.I):
        return False

    strong_event = re.search(
        r"(?:사고|재해).{0,45}(?:발생|사망|부상)|"
        r"(?:추락|협착|감전|붕괴|폭발|화재|매몰|깔림|충돌|끼임).{0,45}(?:발생|사망|부상)|"
        r"(?:사망|부상)한?\s*(?:사고|재해)|"
        r"\d{1,2}\s*(?::|시)\s*\d{0,2}\s*(?:분)?경",
        after,
        re.I,
    )
    return bool(strong_event or start < 350)


def extract_domestic_accident_date(
    content_text: str | None,
    title: str | None = None,
) -> str | None:
    """국내재해 본문에서 실제 사고일로 근거가 분명한 날짜만 반환한다.

    - 명시된 사고 날짜 라벨의 유효한 연월일을 최우선 사용한다.
    - 등록일·설치일·게시일·행사일·파일경로 날짜는 제외한다.
    - 안내·자료·사례모음처럼 단일 사고가 아닌 문서는 NULL로 둔다.
    - 불완전한 날짜 라벨이 있어도 본문 첫 사고서술에 완전한 날짜가
      명확히 있으면 그 날짜를 사용한다.
    """
    if not content_text:
        return None
    normalized_title = normalize_text(title or "")
    if normalized_title and DOMESTIC_NON_ACCIDENT_TITLE_RE.search(normalized_title):
        return None

    search_area = content_text[:1800]
    all_metadata = list(DOMESTIC_ALL_METADATA_RE.finditer(search_area))
    date_labels = list(DOMESTIC_DATE_LABEL_RE.finditer(search_area))

    # 명시 라벨 안에 완전한 날짜가 있으면 바로 사용한다.
    for label_match in date_labels:
        value_start = label_match.end()
        following = [m.start() for m in all_metadata if m.start() > value_start]
        value_end = min(following) if following else min(len(search_area), value_start + 120)
        value = search_area[value_start:value_end]
        matches = [
            row for row in _iter_valid_date_matches(
                value, DOMESTIC_FULL_DATE_PATTERNS, limit=min(len(value) + 1, 60)
            )
            if row[0] <= 3
        ]
        if matches:
            return matches[0][2].isoformat()

    accepted: list[tuple[int, date]] = []
    for candidate_start, _priority, parsed, match in _iter_valid_date_matches(
        search_area, DOMESTIC_FULL_DATE_PATTERNS, limit=len(search_area) + 1
    ):
        if _domestic_date_candidate_is_accident(search_area, candidate_start, match):
            accepted.append((candidate_start, parsed))

    if not accepted:
        return None

    # 같은 날짜 반복은 하나로 보고, 서로 다른 사고일이 여러 개면 사례모음 가능성이
    # 있으므로 제목/본문 첫 사고서술이 분명한 첫 날짜만 사용한다.
    unique: list[tuple[int, date]] = []
    seen: set[str] = set()
    for candidate_start, parsed in accepted:
        iso = parsed.isoformat()
        if iso not in seen:
            seen.add(iso)
            unique.append((candidate_start, parsed))

    first_start, first_date = unique[0]
    if len(unique) > 1 and first_start >= 350:
        return None
    return first_date.isoformat()


def extract_full_date_from_body(content_text: str | None) -> str | None:
    return _extract_iso_date(content_text, FULL_DATE_PATTERNS, 600)


def normalize_title_date(title_date_text: str | None, content_text: str | None) -> str | None:
    """제목 날짜도 최종 컬럼에는 ISO만 저장한다.

    제목이 월/일만 제공할 때는 본문 앞부분에 4자리 연도가 정확히 하나 있는 경우에만 결합한다.
    연도를 확인할 수 없으면 추측하지 않고 NULL을 반환한다.
    """
    if not title_date_text:
        return None
    full = _extract_iso_date(title_date_text, FULL_DATE_PATTERNS, len(title_date_text) + 1)
    if full:
        return full
    match = TITLE_MONTH_DAY_RE.fullmatch(title_date_text)
    if not match or not content_text:
        return None
    years = {int(value) for value in YEAR_RE.findall(content_text[:600])}
    if len(years) != 1:
        return None
    try:
        return date(next(iter(years)), int(match.group("month")), int(match.group("day"))).isoformat()
    except ValueError:
        return None


def _normalize_admin_token(token: str) -> str:
    return re.sub(r"\s+", "", token).strip(" ,.;:：()[]{}")


def _complete_sigungu_suffix(token: str, province: str | None) -> str:
    if token.endswith(("시", "군", "구")):
        return token
    if province:
        matches = [name for name in PROVINCE_SIGUNGU[province] if name[:-1] == token]
    else:
        matches = [name for name in SIGUNGU_TO_PROVINCES if name[:-1] == token]
    return matches[0] if len(matches) == 1 else token


def _validate_sigungu(province: str | None, sigungu: str | None) -> bool:
    if sigungu is None:
        return True
    city, *district = sigungu.split()
    if province:
        if city not in PROVINCE_SIGUNGU[province]:
            return False
        if district:
            return district[0] in CITY_DISTRICTS.get((province, city), set())
        return True
    if city not in SIGUNGU_TO_PROVINCES:
        return False
    if district:
        return any(
            district[0] in CITY_DISTRICTS.get((candidate, city), set())
            for candidate in SIGUNGU_TO_PROVINCES[city]
        )
    return True


def parse_location(value: Any) -> LocationParts:
    raw = normalize_nullable_text(value)
    if raw is None:
        return LocationParts(None, None, None, None, False, None)

    cleaned = normalize_text(raw)
    cleaned = re.sub(r"[\[\](){}]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:：")
    if not cleaned:
        return LocationParts(None, None, None, None, False, raw)

    tokens = [_normalize_admin_token(token) for token in cleaned.split() if _normalize_admin_token(token)]
    if not tokens:
        return LocationParts(None, None, None, None, False, raw)

    sido: str | None = None
    sigungu: str | None = None
    detail_tokens: list[str] = []
    index = 0

    if tokens[0] in PROVINCE_ALIASES:
        sido = PROVINCE_ALIASES[tokens[0]]
        index = 1

    if index < len(tokens):
        first = _complete_sigungu_suffix(tokens[index], sido)
        if first.endswith(("시", "군", "구")):
            sigungu = first
            index += 1
            if index < len(tokens) and first.endswith("시") and tokens[index].endswith("구"):
                sigungu = f"{first} {tokens[index]}"
                index += 1

    for token in tokens[index:]:
        if token.endswith(("읍", "면", "동", "리", "가")):
            detail_tokens.append(token)
        else:
            break

    valid = bool(sido or sigungu) and _validate_sigungu(sido, sigungu)
    if sido and sigungu is None:
        valid = True

    detail = " ".join(detail_tokens) or None
    location = " ".join(value for value in (sido, sigungu, detail) if value) or None
    return LocationParts(location, sido, sigungu, detail, valid, raw)


def normalize_location(value: Any) -> str | None:
    parsed = parse_location(value)
    return parsed.location if parsed.valid else None


def split_location_components(value: Any) -> tuple[str | None, str | None, str | None, str | None]:
    parsed = parse_location(value)
    if not parsed.valid:
        return None, None, None, None
    return parsed.location, parsed.sido, parsed.sigungu, parsed.detail


def _parse_valid_upper_location_prefix(value: str, source_text: str | None = None) -> LocationParts:
    """명시 위치값의 하위 구·동이 오타/과거명이어도 유효한 시·도+시·군·구까지만 보존한다.

    전체 값이 이미 유효하면 그대로 유지한다. 전체 값이 비유효한 경우에만 끝 토큰을
    제거하면서 유효한 상위 위치를 찾고, 시·군·구까지 확인된 후보만 반환한다.
    따라서 `서울시 은평동 응암동`처럼 계층이 불명확한 값은 자동 보정하지 않는다.
    """
    raw = normalize_nullable_text(value)
    if raw is None:
        return LocationParts(None, None, None, None, False, None)

    exact = _sanitize_location_parts(parse_location(raw), source_text or raw)
    if exact.valid:
        return exact

    cleaned = normalize_text(raw)
    cleaned = re.sub(r"[\[\](){}]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:：")
    tokens = [token for token in cleaned.split() if token]
    for length in range(len(tokens) - 1, 0, -1):
        candidate = _sanitize_location_parts(
            parse_location(" ".join(tokens[:length])),
            source_text or cleaned,
        )
        if candidate.valid and candidate.sigungu:
            return candidate
    return LocationParts(None, None, None, None, False, raw)


def _location_prefix_from_labeled_value(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip(" \n\t:：,.;■◆●▶-")
    cleaned = re.sub(r"^\d+\.\s*", "", cleaned)

    # `지역: 경기도 안성`처럼 시·군 접미사가 생략된 명시 라벨은
    # 같은 시·도 안에서 어간이 하나로 확정될 때만 접미사를 보완한다.
    bare = re.match(
        rf"^(?P<province>{PROVINCE_TOKEN_ALT})\s+(?P<stem>{STRICT_SIGUNGU_STEM_ALT})(?=\s|$)",
        cleaned,
    )
    if bare:
        province_raw = bare.group("province")
        province = PROVINCE_ALIASES[province_raw]
        stem = bare.group("stem")
        names = [name for name in PROVINCE_SIGUNGU[province] if name[:-1] == stem]
        if len(names) == 1:
            cleaned = f"{province_raw} {names[0]}" + cleaned[bare.end():]

    for pattern in (PROVINCE_CANDIDATE_RE, CITY_CANDIDATE_RE):
        match = pattern.search(cleaned)
        if not match or match.start() > 2:
            continue
        parsed = _parse_valid_upper_location_prefix(match.group("location"), cleaned)
        if parsed.valid:
            return parsed.location
    return None


def extract_explicit_location_from_body(content_text: str | None, *, limit: int = 1800) -> str | None:
    """행정구역과 현장 근거가 함께 있는 위치만 본문에서 추출한다."""
    if not content_text:
        return None
    search_area = re.sub(r"\s+", " ", content_text[:limit])
    candidates: list[tuple[int, int, str]] = []

    for pattern in (PROVINCE_CANDIDATE_RE, CITY_CANDIDATE_RE):
        for match in pattern.finditer(search_area):
            raw_candidate = match.group("location")
            parsed = parse_location(raw_candidate)
            if not parsed.valid:
                continue
            tail = search_area[match.end(): match.end() + 140]
            if not LOCATION_EVIDENCE_RE.search(tail):
                continue
            candidates.append((match.start(), -parsed.specificity, parsed.location or ""))

    return min(candidates)[2] if candidates else None


def _extract_domestic_accident_header_location(content_text: str | None) -> str | None:
    """초기 재해개요의 `날짜·시간, 지역,` 구조에서 명확한 위치만 보완한다.

    일반 본문 전체를 추정하지 않고 첫 600자 안에서 시간 바로 뒤에 오는 위치만 본다.
    유효한 시·군·구가 확인되는 경우에만 반환한다.
    """
    if not content_text:
        return None
    search_area = re.sub(r"\s+", " ", content_text[:600])
    time_re = re.compile(
        rf"(?:'\d{{2}}\s*[.]\s*\d{{1,2}}\s*[.]\s*\d{{1,2}}\s*[.]?\s*)?"
        rf"\d{{1,2}}:\d{{2}}\s*(?:분)?경\s*,?\s*"
        rf"(?P<location>(?:{PROVINCE_TOKEN_ALT})(?:\s+{ADMIN_TOKEN}){{0,4}})"
        rf"(?=\s*[,，]|\s+소재)"
    )
    for match in time_re.finditer(search_area):
        parsed = _parse_valid_upper_location_prefix(match.group("location"), search_area)
        if parsed.valid and parsed.sigungu:
            return parsed.location
    return None


def extract_domestic_location(content_text: str | None) -> str | None:
    """국내재해의 기존 필드는 건드리지 않고 날짜·위치만 별도 추출한다."""
    if not content_text:
        return None

    metadata_matches = list(DOMESTIC_ALL_METADATA_RE.finditer(content_text))
    target_labels = {_compact_label(label) for label in DOMESTIC_LOCATION_LABELS}
    for index, match in enumerate(metadata_matches):
        if _compact_label(match.group("label")) not in target_labels:
            continue
        start = match.end()
        end = metadata_matches[index + 1].start() if index + 1 < len(metadata_matches) else len(content_text)
        marker = DOMESTIC_LOCATION_MARKER_RE.search(content_text, start, end)
        if marker:
            end = marker.start()
        location = _location_prefix_from_labeled_value(content_text[start:end])
        if location:
            return location

    explicit = extract_explicit_location_from_body(content_text, limit=1800)
    if explicit:
        return explicit
    return _extract_domestic_accident_header_location(content_text)


def _edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, 1):
        current = [i]
        for j, char_right in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (char_left != char_right)))
        previous = current
    return previous[-1]


def _sigungu_city(value: str | None) -> str | None:
    return value.split()[0] if value else None


def _same_location_hierarchy(left: LocationParts, right: LocationParts) -> bool:
    left_city = _sigungu_city(left.sigungu)
    right_city = _sigungu_city(right.sigungu)

    if left.sido and right.sido and left.sido != right.sido:
        # 사고사망 제목의 `광주 능평동`처럼 광역시 약칭과 경기도 광주시가
        # 구분되지 않는 경우에는 본문의 명확한 행정구역을 우선할 수 있게 한다.
        left_raw = normalize_text(left.raw or "")
        right_raw = normalize_text(right.raw or "")
        if not (
            (left_raw.startswith("광주 ") and right_city == "광주시")
            or (right_raw.startswith("광주 ") and left_city == "광주시")
        ):
            return False

    if left_city and right_city and left_city != right_city:
        return False
    return True


def resolve_fatal_location(
    title_location: str | None,
    content_text: str | None,
) -> tuple[LocationParts, str]:
    """제목·본문 위치를 보수적으로 합친다.

    - 제목이 유효하고 본문과 같은 계층이면 더 구체적인 값을 사용한다.
    - 제목 지역이 오타/비유효이고 본문 지역이 유효하면 본문을 사용한다.
    - 둘 다 유효하지만 실제 행정구역이 다르면 NULL로 둔다.
    """
    title = parse_location(title_location)
    body = parse_location(extract_explicit_location_from_body(content_text))

    if title.valid and body.valid:
        if _same_location_hierarchy(title, body):
            return (body if body.specificity > title.specificity else title), "consistent"
        return LocationParts(None, None, None, None, False, None), "conflict"

    if title.valid:
        return title, "title"
    if body.valid:
        if title.sido and body.sido == title.sido and title.sigungu and body.sigungu:
            left = title.sigungu.replace(" ", "")
            right = body.sigungu.replace(" ", "")
            if _edit_distance(left, right) <= 1:
                return body, "body_corrected_title_typo"
        return body, "body"

    # 제목에 유효한 시·도만 확인되고 하위 지역만 잘못된 경우 시·도까지는 보존한다.
    if title.sido:
        province_only = LocationParts(title.sido, title.sido, None, None, True, title.raw)
        return province_only, "title_province_only"
    return LocationParts(None, None, None, None, False, None), "none"



def _normalize_strict_location_search(text: str) -> str:
    search_area = re.sub(r"\s+", " ", text)
    search_area = LOCATION_TIME_PROVINCE_RE.sub(r"\1 ", search_area)
    search_area = re.sub(
        r"([가-힣0-9]{1,16}(?:시|군|구|읍|면|동|리))"
        r"(에서|에는|으로|까지|부터|에|소재)(?=\s|[A-Z○㈜△□])",
        r"\1 \2",
        search_area,
    )
    search_area = re.sub(
        r"(?<![가-힣])광주시?\s+(?P<detail>[가-힣0-9]{1,16}(?:읍|면))(?![가-힣0-9])",
        r"경기도 광주시 \g<detail>",
        search_area,
    )

    province_bare_re = re.compile(
        rf"(?<![가-힣])(?P<province>{PROVINCE_TOKEN_ALT})\s+"
        rf"(?P<stem>{STRICT_SIGUNGU_STEM_ALT})"
        rf"(?=\s+(?:소재|내|현장|사업장|공장|작업장|공사))"
    )

    def replace_bare(match: re.Match[str]) -> str:
        province_raw = match.group("province")
        province = PROVINCE_ALIASES[province_raw]
        stem = match.group("stem")
        names = [name for name in PROVINCE_SIGUNGU[province] if name[:-1] == stem]
        return f"{province_raw} {names[0]}" if len(names) == 1 else match.group(0)

    return province_bare_re.sub(replace_bare, search_area)


def _strict_location_candidates(
    content_text: str | None,
    *,
    limit: int = 1800,
) -> list[tuple[int, int, int, bool, LocationParts]]:
    if not content_text:
        return []
    search_area = _normalize_strict_location_search(content_text[:limit])
    rows: dict[tuple[int, str], tuple[int, int, int, bool, LocationParts]] = {}

    for pattern in STRICT_LOCATION_PATTERNS:
        for match in pattern.finditer(search_area):
            parsed = parse_location(match.group("location"))
            parsed = _sanitize_location_parts(parsed, search_area)
            if not parsed.valid:
                continue
            tail = search_area[match.end():match.end() + 150]
            evidence = LOCATION_EVIDENCE_RE.search(tail)
            if not evidence:
                continue
            # `강화`, `처리`처럼 일반 단어와 겹치는 시군구 어간은
            # 바로 뒤에 위치 근거가 붙을 때만 지역으로 인정한다.
            if pattern is STRICT_CITY_STEM_RE:
                if match.start() > 500 or not re.match(r"\s*(?:에\s*)?(?:소재|내)", tail):
                    continue
            before = search_area[max(0, match.start() - 90):match.start()]
            affiliation = bool(
                LOCATION_AFFILIATION_RE.search(before)
                or LOCATION_AFFILIATION_AFTER_RE.search(tail)
            )
            score = parsed.specificity + 2
            if LOCATION_SITE_RE.search(tail):
                score += 8
            if affiliation:
                score -= 12
            row = (score, parsed.specificity, match.start(), affiliation, parsed)
            key = (match.start(), parsed.location or "")
            old = rows.get(key)
            if old is None or row[:2] > old[:2]:
                rows[key] = row
    return list(rows.values())


def _strict_best_body_location(
    content_text: str | None,
    *,
    limit: int = 1800,
) -> tuple[LocationParts, int, bool, list[tuple[int, int, int, bool, LocationParts]]]:
    candidates = _strict_location_candidates(content_text, limit=limit)
    if not candidates:
        return LocationParts(None, None, None, None, False, None), -999, False, []

    best = max(candidates, key=lambda row: (row[0], row[1], -row[2]))
    tied = [
        row for row in candidates
        if row is not best
        and row[0] == best[0]
        and not _same_location_hierarchy(row[4], best[4])
    ]
    if tied:
        return LocationParts(None, None, None, None, False, None), best[0], False, candidates
    return best[4], best[0], best[3], candidates


def _detail_token_is_standalone(source_text: str, token: str) -> bool:
    return bool(re.search(
        rf"(?<![가-힣0-9]){re.escape(token)}(?:\d+)?"
        rf"(?:에서|에는|으로|까지|부터|에|소재)?(?![가-힣0-9])",
        _normalize_strict_location_search(source_text),
    ))


def _sanitize_location_parts(parts: LocationParts, source_text: str) -> LocationParts:
    if not parts.valid:
        return parts

    detail_tokens = []
    normalized_source = _normalize_strict_location_search(source_text)
    for token in (parts.detail or "").split():
        if LOCATION_PLACEHOLDER_DETAIL_RE.search(token):
            continue
        if token in NON_ADMIN_DETAIL_TOKENS:
            continue
        if _detail_token_is_standalone(source_text, token):
            detail_tokens.append(token)
            continue

        # 숫자·번지·과거 동명 표기처럼 명확한 오염 근거가 없는 값은 유지한다.
        # 시설명·업종명의 접두사로 잘린 것이 확인될 때만 제거한다.
        false_prefix_matches = re.finditer(
            rf"(?<![가-힣0-9]){re.escape(token)}(?P<tail>[가-힣0-9]+)",
            normalized_source,
        )
        if any(
            FALSE_DETAIL_CONTINUATION_RE.match(match.group("tail"))
            for match in false_prefix_matches
        ):
            continue
        detail_tokens.append(token)

    # 시·도 바로 아래 상세지역은 한 개까지만 허용한다.
    # 과거 표기인 `울산시 삼남면 삼막리`처럼 읍·면+리 조합만 두 개를 허용한다.
    if parts.sido and not parts.sigungu and len(detail_tokens) > 1:
        if not (
            len(detail_tokens) == 2
            and detail_tokens[0].endswith(("읍", "면"))
            and detail_tokens[1].endswith("리")
        ):
            return LocationParts(None, None, None, None, False, parts.raw)

    detail = " ".join(detail_tokens) or None
    location = " ".join(value for value in (parts.sido, parts.sigungu, detail) if value) or None
    valid = bool(parts.sido or parts.sigungu)
    return LocationParts(location, parts.sido, parts.sigungu, detail, valid, parts.raw)


def _domestic_location_label_exists(content_text: str | None) -> bool:
    if not content_text:
        return False
    targets = {_compact_label(label) for label in DOMESTIC_LOCATION_LABELS}
    return any(
        _compact_label(match.group("label")) in targets
        for match in DOMESTIC_ALL_METADATA_RE.finditer(content_text)
    )


def _candidate_matching_location(
    candidates: list[tuple[int, int, int, bool, LocationParts]],
    selected: LocationParts,
) -> tuple[int, int, int, bool, LocationParts] | None:
    matches = [
        row for row in candidates
        if _same_location_hierarchy(row[4], selected)
    ]
    return max(matches, key=lambda row: (row[0], row[1], -row[2])) if matches else None



def _source_has_explicit_missing_component_pattern(
    content_text: str | None,
    best: LocationParts,
) -> bool:
    if not content_text or not best.valid:
        return False
    raw = re.sub(r"\s+", " ", content_text[:1800])

    if LOCATION_TIME_PROVINCE_RE.search(raw):
        return True

    if best.sido and best.sigungu:
        city = best.sigungu.split()[0]
        stem = city[:-1]
        aliases = [
            alias for alias, canonical in PROVINCE_ALIASES.items()
            if canonical == best.sido
        ]
        alias_alt = "|".join(sorted((re.escape(value) for value in aliases), key=len, reverse=True))
        if alias_alt and re.search(
            rf"(?<![가-힣])(?:{alias_alt})\s+{re.escape(stem)}"
            rf"(?=\s+(?:소재|내|현장|사업장|공장|작업장|공사))",
            raw,
        ):
            return True
    return False


def resolve_domestic_location_verified(
    initial_location: str | None,
    content_text: str | None,
) -> LocationParts:
    initial = _sanitize_location_parts(parse_location(initial_location), content_text or "")
    best, best_score, _best_affiliation, candidates = _strict_best_body_location(content_text)

    if _domestic_location_label_exists(content_text):
        if not initial.valid:
            return LocationParts(None, None, None, None, False, None)
        if LOCATION_ROUTE_RE.search(_normalize_strict_location_search((content_text or "")[:1800])):
            # 도로·구간 문서에서 라벨 위치와 시작/종료 지역이 섞이면
            # 실제 사고지 하나를 확정할 수 없으므로 추측하지 않는다.
            return LocationParts(None, None, None, None, False, None)
        return initial

    if initial.valid:
        matching = _candidate_matching_location(candidates, initial)
        better_accident = [
            row for row in candidates
            if row[0] >= 7
            and not row[3]
            and not _same_location_hierarchy(row[4], initial)
            and (matching is None or row[0] > matching[0])
        ]
        if matching and matching[3] and better_accident:
            return max(better_accident, key=lambda row: (row[0], row[1], -row[2]))[4]

        if (
            best.valid
            and _same_location_hierarchy(initial, best)
            and best.specificity > initial.specificity
            and _source_has_explicit_missing_component_pattern(content_text, best)
        ):
            return best
        return initial

    if best.valid and best_score >= 7:
        return best
    return LocationParts(None, None, None, None, False, None)


def _parse_verified_title_location(title_location: str | None) -> LocationParts:
    normalized = normalize_text(title_location or "")
    if FATAL_JOINED_GYEONGGI_GWANGJU_RE.fullmatch(normalized):
        return parse_location("경기도 광주시")
    special = re.fullmatch(r"광주시?\s+(?P<detail>[가-힣0-9]{1,16}(?:읍|면))", normalized)
    if special:
        return parse_location(f"경기도 광주시 {special.group('detail')}")
    return _sanitize_location_parts(parse_location(title_location), normalized)


def _inherit_title_sido(body: LocationParts, title: LocationParts) -> LocationParts:
    """본문에 시·군·구만 있고 제목에 같은 지역의 시·도가 있으면 시·도만 보완한다."""
    if not body.valid or body.sido or not title.sido:
        return body
    if not _same_location_hierarchy(title, body):
        return body
    location = " ".join(value for value in (title.sido, body.sigungu, body.detail) if value)
    return LocationParts(location, title.sido, body.sigungu, body.detail, True, body.raw)


def resolve_fatal_location_verified(
    title_location: str | None,
    content_text: str | None,
) -> tuple[LocationParts, str]:
    # 기존 정상 동작을 기준으로 두고, 확인된 오류 유형만 후처리한다.
    initial, initial_decision = resolve_fatal_location(title_location, content_text)
    initial = _sanitize_location_parts(
        initial,
        "\n".join(value for value in (title_location or "", content_text or "") if value),
    )
    body, _score, _affiliation, _candidates = _strict_best_body_location(content_text)
    verified_title = _parse_verified_title_location(title_location)
    body = _inherit_title_sido(body, verified_title)

    # `광주 초월읍`처럼 광역시로 볼 수 없고 읍·면 문맥이 명확한 경우.
    normalized_title = normalize_text(title_location or "")
    if re.fullmatch(r"광주시?\s+[가-힣0-9]{1,16}(?:읍|면)", normalized_title):
        if body.valid and _same_location_hierarchy(verified_title, body):
            return body, "consistent"
        return verified_title, "title"

    # `경기광주`처럼 제목 자체만으로 유효하게 정규화되고 본문 위치가 없는 경우.
    if verified_title.valid and not body.valid and not initial.valid:
        return verified_title, "title"

    # 기존 결과가 NULL인 경우에도 제목과 본문이 같은 지역이거나
    # 제목 자체가 비유효할 때만 본문으로 보완한다. 실제 충돌은 NULL 유지.
    if not initial.valid and body.valid:
        if not verified_title.valid:
            return body, "body"
        if _same_location_hierarchy(verified_title, body):
            return body, "consistent"
        return LocationParts(None, None, None, None, False, None), "conflict"

    if initial.valid and body.valid and _same_location_hierarchy(initial, body):
        repeated_stem = bool(
            initial.detail
            and initial.sigungu
            and initial.detail == initial.sigungu.split()[0][:-1]
        )
        if repeated_stem:
            return body, "consistent"

    return initial, initial_decision


def location_detail_is_source_backed(source_text: str | None, detail: str | None) -> bool:
    if not detail:
        return True
    if not source_text:
        return False
    return all(_detail_token_is_standalone(source_text, token) for token in detail.split())


def location_detail_is_suspicious(detail: str | None) -> bool:
    """시설명·업종명·마스킹 일부가 상세 행정구역으로 저장됐는지 검사한다."""
    if not detail:
        return False
    return any(
        token in NON_ADMIN_DETAIL_TOKENS
        or LOCATION_PLACEHOLDER_DETAIL_RE.search(token) is not None
        for token in detail.split()
    )


def location_has_affiliation_priority_error(
    content_text: str | None,
    location: str | None,
) -> bool:
    if not content_text or not location:
        return False
    selected = parse_location(location)
    if not selected.valid:
        return True
    _best, _score, _affiliation, candidates = _strict_best_body_location(content_text)
    matching = _candidate_matching_location(candidates, selected)
    if not matching or not matching[3]:
        return False
    return any(
        row[0] >= 7
        and not row[3]
        and row[0] > matching[0]
        and not _same_location_hierarchy(row[4], selected)
        for row in candidates
    )


def preprocess_domestic_item(item: dict[str, Any]) -> dict[str, Any]:
    boardno = normalize_text(item.get("boardno"))
    business = normalize_text(item.get("business")) or None
    title = normalize_text(item.get("keyword"))
    raw_value = item.get("contents")
    content_raw = None if raw_value is None else str(raw_value)
    content_text = html_to_text(content_raw)

    if not boardno:
        raise ValueError("국내재해사례 항목에 boardno가 없습니다.")
    if not title:
        raise ValueError(f"국내재해사례 boardno={boardno}에 제목이 없습니다.")

    initial_location = extract_domestic_location(content_text)
    location = resolve_domestic_location_verified(initial_location, content_text)
    return {
        "id": None,
        "title": title,
        "accident_date_text": extract_domestic_accident_date(content_text, title),
        "location": location.location if location.valid else None,
        "location_sido": location.sido if location.valid else None,
        "location_sigungu": location.sigungu if location.valid else None,
        "location_detail": location.detail if location.valid else None,
        "content_text": content_text,
        "content_raw": content_raw,
        "boardno": boardno,
        "business": business,
        "detailed_business": extract_labeled_value(content_text, "업종"),
        "causal_object": extract_labeled_value(content_text, "기인물"),
    }


def parse_fatal_title(keyword: Any) -> tuple[str, str | None, str | None, str]:
    title_raw = normalize_text(keyword)
    if not title_raw:
        raise ValueError("사고사망 항목에 keyword 제목이 없습니다.")
    match = FATAL_TITLE_RE.match(title_raw)
    if not match:
        return title_raw, None, None, title_raw
    date_text = normalize_text(match.group("date")) or None
    location = normalize_text(match.group("location")) or None
    title = normalize_text(match.group("title")) or title_raw
    return title_raw, date_text, location, title


def _merge_fatal_lines(text: str) -> str:
    lines = [line.strip() for line in normalize_text(text).splitlines() if line.strip()]
    merged: list[str] = []
    province_tokens = set(PROVINCE_ALIASES)
    modifiers = {"이던", "밟은", "인근", "있던", "하던", "중인", "위한", "의한"}

    for line in lines:
        if line in {".", ",", "·", "-"} and not merged:
            continue
        if merged and merged[-1].count("[") > merged[-1].count("]"):
            merged[-1] = f"{merged[-1]} {line}".strip()
            continue
        if merged and merged[-1] in province_tokens:
            merged[-1] = f"{merged[-1]} {line}".strip()
            continue
        if merged and line in modifiers:
            merged[-1] = f"{merged[-1]}{line}"
            continue
        if merged and line in {"재해자가", "작업자가", "근로자가"} and len(merged[-1]) <= 20:
            merged[-1] = f"{merged[-1]} {line}"
            continue
        merged.append(line)

    result = "\n".join(merged)
    result = re.sub(
        r"(\d{1,2}:\d{2})\s*경(?=(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주))",
        r"\1경\n",
        result,
    )
    result = re.sub(r"\)※", ")\n※ ", result)
    result = repair_safe_glued_boundaries(result)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def normalize_fatal_content(raw_value: Any) -> str | None:
    raw = normalize_nullable_text(raw_value)
    if raw is None:
        return None
    text = _html_to_lines(raw) if re.search(r"<[^>]+>", raw) else raw
    cleaned = normalize_text(_merge_fatal_lines(text))
    return cleaned or None


def preprocess_fatal_item(item: dict[str, Any]) -> dict[str, Any]:
    title_raw, title_date_text, title_location, title = parse_fatal_title(item.get("keyword"))
    raw_value = item.get("contents")
    content_raw = None if raw_value is None else str(raw_value)
    content_text = normalize_fatal_content(content_raw)

    body_date = extract_full_date_from_body(content_text)
    accident_date_text = body_date or normalize_title_date(title_date_text, content_text)
    location, _decision = resolve_fatal_location_verified(title_location, content_text)

    return {
        "id": None,
        "title": title,
        "accident_date_text": accident_date_text,
        "location": location.location if location.valid else None,
        "location_sido": location.sido if location.valid else None,
        "location_sigungu": location.sigungu if location.valid else None,
        "location_detail": location.detail if location.valid else None,
        "content_text": content_text,
        "content_raw": content_raw,
        "title_raw": title_raw,
    }
