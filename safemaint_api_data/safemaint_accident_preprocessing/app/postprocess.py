from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.admin_areas import CITY_DISTRICTS, PROVINCE_ALIASES, PROVINCE_SIGUNGU, SIGUNGU_TO_PROVINCES
from app.historical_admin import (
    ADMIN_TYPO_ALIASES,
    AMBIGUOUS_HISTORICAL_DETAILS,
    DETAIL_TO_PARENT,
    HISTORICAL_SIGUNGU_ALIASES,
    INCHEON_OLD_NAMGU_DETAIL_TO_DISTRICT,
    NAMED_AREA_ALIASES,
    SEOUL_DISTRICTS,
)
from app.preprocess import (
    LocationParts,
    normalize_text,
    parse_fatal_title,
    parse_location,
    repair_safe_glued_boundaries,
)

# 기존 preprocess.py는 변경하지 않는다. 이 모듈은 그 결과에 허용된 컬럼만 독립 후처리한다.
ALLOWED_POSTPROCESS_COLUMNS = {
    "accident_date_text",
    "location",
    "location_sido",
    "location_sigungu",
    "location_detail",
    "content_text",
}

NON_ACCIDENT_TITLE_RE = re.compile(
    r"^\s*(?:\[[^\]]*(?:세미나|간담회|교육|행사|공지|안내|자료)[^\]]*\]|"
    r"(?:세미나|간담회|교육|행사|공지|안내|자료|압축파일|사례모음|통계|목록|다운로드)(?:\s|$))",
    re.I,
)

# 명시 위치 라벨. 글자 사이 공백도 허용한다.
def _spaced(word: str) -> str:
    return r"\s*".join(re.escape(ch) for ch in re.sub(r"\s+", "", word))

LOCATION_LABELS = ("소재지", "발생장소", "사고장소", "재해발생장소", "재해장소", "지역")
LOCATION_LABEL_ALT = "|".join(sorted((_spaced(v) for v in LOCATION_LABELS), key=len, reverse=True))
LOCATION_LABEL_RE = re.compile(rf"(?:【\s*)?(?P<label>{LOCATION_LABEL_ALT})(?:\s*】)?\s*[:：]", re.I)
NEXT_METADATA_RE = re.compile(
    r"(?:【\s*)?(?:시\s*공\s*사|공\s*사\s*명|피\s*재\s*자|피\s*해\s*자|"
    r"사\s*고\s*유\s*형|피\s*해\s*정\s*도|공\s*사\s*규\s*모|재\s*해\s*내\s*용|"
    r"재\s*해\s*개\s*요|재\s*해\s*원\s*인|원\s*인|대\s*책)(?:\s*】)?\s*[:：]",
    re.I,
)
SECTION_RE = re.compile(r"\s+(?:\d+\s*[.)]|[■□◆●▶])\s*(?:사고경위|재해|작업|원인|대책|공사|$)", re.I)

PROVINCE_ALT = "|".join(sorted((re.escape(v) for v in PROVINCE_ALIASES), key=len, reverse=True))
CURRENT_ADMIN_TOKEN_RE = re.compile(r"^[가-힣0-9]+(?:시|군|구|읍|면|동|리|가)$")
LOT_OR_FACILITY_RE = re.compile(
    r"(?:\d+(?:-\d+)?(?:번지)?|\d+B/L|B/L|일원|택지지구|신도시|교내|우체국|기도원|"
    r"공사현장|사업장|공장|현장|아파트|업소|리조트|운동장|조선소|공단|학교|대학교)",
    re.I,
)
MASK_RE = re.compile(r"^(?:O{2,}|0{2,}|○{2,}|X{2,})", re.I)
# 읍·면·동·리처럼 끝나지만 실제로는 시설·업종명의 앞부분인 값.
# 단독 행정 토큰인지 원문 경계까지 확인하므로 동일 패턴의 신규 데이터에도 적용된다.
SUSPICIOUS_DETAIL_TOKENS = {
    "공동", "자동", "수리", "남동", "폐기업처리", "철근콘크리", "수성구민운동",
}
ROUTE_RE = re.compile(r"에서[, ]{0,4}[가-힣0-9]{2,16}(?:시|군|구)\s+[가-힣0-9]{1,16}간.{0,100}?(?:관로|배관|도로|구간)")
AFFILIATION_BEFORE_RE = re.compile(r"(?:소속업체|소속 업체|본사|업체 주소|사업주 주소).{0,45}$")
AFFILIATION_AFTER_RE = re.compile(r"^(?:.{0,70}?(?:소속의|소속)\s*(?:재해자|피재자|작업자|근로자)|.{0,80}?소재.{0,50}?소속)")
SITE_EVIDENCE_RE = re.compile(r"(?:사고|재해|발생|공사현장|사업장|작업장|공장|업소|주차장|아파트|현장)")

CONTROL_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
STANDALONE_NOISE_LINE_RE = re.compile(r"^\s*(?:\.|다\.)\s*$")
DATE_START_RE = re.compile(r"^(?:19|20)\d{2}\s*[./년]")

# v9 이후 실제 전수검사에서 확인된 안전한 문장 경계. ID가 아니라 일반 패턴이다.
EXTRA_SAFE_SPACING_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(※)(?=위\s*내용)"), r"\1 "),
    # 의존명사 `수`와 보조 용언 경계. 전수검사에서 반복 확인된 명백한 띄어쓰기다.
    (re.compile(r"(할|될|걸|줄|볼|올|갈|둘|놓을|막을|받을|알|쓸|클|설)수(?=(?:있|없|[\s.,!?)]|$))"), r"\1 수"),
    (re.compile(r"수(?=(?:있|없)(?:습니다|도록|어|는|다|[\s.,!?)]|$))"), r"수 "),
    (re.compile(r"(보수|조치|확인|대책|보호구|안전장치)(?=없이)"), r"\1 "),
    # 문장 종료 뒤 다음 문장이 붙은 반복 사례.
    (re.compile(r"([.!?)]|습니다\.)(?=(?:유사한|아울러|재해자|작업자|근로자))"), r"\1 "),
    (re.compile(r"(떨어져)(?=(?:낙하하는|재해자|작업자|근로자))"), r"\1 "),
    (re.compile(r"([과와])(?=(?:재해자|작업자|근로자))"), r"\1 "),
    (re.compile(r"(인해)(?=인양)"), r"\1 "),
    (re.compile(r"(작업\s*중)(?=인접한)"), r"\1 "),
    (re.compile(r"(올라가)(?=(?:판넬|고압전선))"), r"\1 "),
    (re.compile(r"(롤러에)(?=부딪혀)"), r"\1 "),
    (re.compile(r"((?:공사현장|사업장|벌목현장|야적장|물류센터|센터)에서)(?=(?:윈치|섬유로프|다른|도보|배수관|화물차량|반응기|적재물|이동|타워크레인))"), r"\1 "),
    (re.compile(r"((?:연결하여|못하여|충격하여))(?=(?:사상|화물차량|중심))"), r"\1 "),
    (re.compile(r"(작업\s*중)(?=인양고리)"), r"\1 "),
    # 날짜·시간·지역이 완전히 붙은 사고 머리말
    (re.compile(r"(\([월화수목금토일]\))(?=\d{1,2}:\d{2}경)"), r"\1 "),
    (re.compile(r"(\d{1,2}:\d{2}경)(?=(?:경기|경남|경북|전남|전북|충남|충북|서울|부산|대구|인천|광주|대전|울산|세종|강원|제주))"), r"\1 "),
    (re.compile(r"(소재)(?=(?:O{2,}|0{2,}|○{2,}|X{2,}))", re.I), r"\1 "),
    (re.compile(r"((?:O{2,}|0{2,}|○{2,}|X{2,}))(?=공사현장)", re.I), r"\1 "),
    (re.compile(r"(공사현장)(?=\d+층)"), r"\1 "),
    (re.compile(r"(\d+층)(?=[가-힣]{1,12}에서)"), r"\1 "),
    (re.compile(r"([가-힣]{1,12}에서)(?=[가-힣]{2,20}공사)"), r"\1 "),
)


@dataclass(frozen=True)
class LocationDecision:
    parts: LocationParts
    status: str
    raw: str | None = None
    reason: str | None = None


def _parts(location: str | None, sido: str | None, sigungu: str | None, detail: str | None, raw: str | None = None) -> LocationParts:
    value = " ".join(v for v in (sido, sigungu, detail) if v) or location
    return LocationParts(value, sido, sigungu, detail, bool(value), raw)


def _clean_location_value(value: str) -> str:
    text = normalize_text(value)
    text = CONTROL_CHARS_RE.sub("", text)
    text = re.sub(r"^\s*\d+\s*[.)]\s*", "", text)
    text = re.split(r"\s+\d+\s*[.)]\s*(?:사고경위|재해|작업|원인|대책)", text, maxsplit=1, flags=re.I)[0]
    text = re.split(r"\s+[■□◆●▶]", text, maxsplit=1)[0]
    text = text.strip(" \t\n,.;:：-_")
    # 주소 숫자·시설명부터는 행정구역이 아니므로 잘라낸다.
    tokens = text.split()
    kept: list[str] = []
    for token in tokens:
        token = token.strip(" ,.;:：()[]{}")
        if not token:
            continue
        if LOT_OR_FACILITY_RE.search(token) and not CURRENT_ADMIN_TOKEN_RE.fullmatch(token):
            break
        kept.append(token)
        if len(kept) >= 6:
            break
    return " ".join(kept)


def extract_labeled_location_raw(content_text: str | None) -> list[str]:
    if not content_text:
        return []
    rows: list[str] = []
    matches = list(LOCATION_LABEL_RE.finditer(content_text[:3500]))
    for match in matches:
        start = match.end()
        candidates = [m.start() for m in NEXT_METADATA_RE.finditer(content_text, start, min(len(content_text), start + 500))]
        end = min(candidates) if candidates else min(len(content_text), start + 240)
        section = SECTION_RE.search(content_text, start, end)
        if section:
            end = section.start()
        raw = _clean_location_value(content_text[start:end])
        if raw:
            rows.append(raw)
    return rows


def clean_content_text_final(text: str | None) -> str | None:
    if not text:
        return None
    value = CONTROL_CHARS_RE.sub("", text)
    value = repair_safe_glued_boundaries(value)
    for pattern, replacement in EXTRA_SAFE_SPACING_RULES:
        value = pattern.sub(replacement, value)
    value = re.sub(r"※\s{2,}위", "※ 위", value)
    lines = [re.sub(r"[^\S\r\n]+", " ", line).strip() for line in value.splitlines()]
    cleaned: list[str] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        if line == ".":
            continue
        if line == "다." and not cleaned and index + 1 < len(lines) and DATE_START_RE.search(lines[index + 1]):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip() or None


def _detail_is_standalone(token: str, source_text: str) -> bool:
    if not source_text:
        return True
    # 토큰이 더 긴 한글·숫자 단어의 접두부로만 나타나면 행정구역이 아니다.
    standalone = re.search(rf"(?<![가-힣0-9]){re.escape(token)}(?![가-힣0-9])", source_text)
    if standalone is not None:
        return True
    # `암사 2동`처럼 숫자 행정동이 띄어 쓰인 원문도 동일 토큰으로 인정한다.
    numbered = re.fullmatch(r"(?P<stem>[가-힣]+)(?P<number>\d+)동", token)
    if numbered:
        pattern = rf"(?<![가-힣0-9]){re.escape(numbered.group('stem'))}\s*{numbered.group('number')}동(?![가-힣0-9])"
        return re.search(pattern, source_text) is not None
    # 과거 하위 행정구역을 현행 명칭으로 바꾼 경우 원문 별칭도 근거로 인정한다.
    for old, current in ADMIN_TYPO_ALIASES.items():
        if current == token and re.search(rf"(?<![가-힣0-9]){re.escape(old)}(?![가-힣0-9])", source_text):
            return True
    return False


def _normalize_detail_token(token: str, source_text: str = "") -> str | None:
    original = token.strip(" ,.;:：()[]{}")
    if not original or MASK_RE.match(original):
        return None
    token = ADMIN_TYPO_ALIASES.get(original, original)
    # 행정동의 숫자 앞 공백은 제거한다. 주소 번지는 저장하지 않는다.
    token = re.sub(r"^([가-힣]+)\s+(\d+동)$", r"\1\2", token)
    token = re.sub(r"^([가-힣]+)\d+동$", r"\1동", token)
    if re.fullmatch(r"\d+(?:-\d+)?(?:번지)?", token):
        return None
    if token in SUSPICIOUS_DETAIL_TOKENS:
        return None
    if token.endswith(("읍", "면", "동", "리", "가")):
        # 숫자 행정동 정규화처럼 표준화 후 문자열이 달라진 경우에는 원문 토큰 경계를 확인한다.
        if not (_detail_is_standalone(token, source_text) or _detail_is_standalone(original, source_text)):
            return None
        return token
    return None


def _collect_detail_tokens(tokens: list[str], start: int, source_text: str, *, limit: int = 2) -> str | None:
    details: list[str] = []
    for token in tokens[start:start + limit]:
        detail = _normalize_detail_token(token, source_text)
        if not detail:
            break
        details.append(detail)
    return " ".join(details) or None


def _current_parts_from_tokens(tokens: list[str], source_text: str) -> LocationDecision | None:
    if not tokens:
        return None

    tokens = [ADMIN_TYPO_ALIASES.get(token, token) for token in tokens]
    # `암사 2동` 같은 분리 토큰 결합
    joined: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and re.fullmatch(r"\d+동", tokens[i + 1]) and re.fullmatch(r"[가-힣]+", tokens[i]):
            joined.append(tokens[i] + tokens[i + 1])
            i += 2
        else:
            joined.append(tokens[i])
            i += 1
    tokens = joined

    # `고성 거류면`처럼 시군 접미사가 생략됐지만 상세 읍면동으로 상위 지역이 확정되는 경우.
    if len(tokens) >= 2 and tokens[1] in DETAIL_TO_PARENT:
        parent_sido, parent_sigungu = DETAIL_TO_PARENT[tokens[1]]
        parent_city = parent_sigungu.split()[0]
        if tokens[0] in {parent_city, parent_city[:-1]}:
            detail = _collect_detail_tokens(tokens, 1, source_text)
            return LocationDecision(_parts(None, parent_sido, parent_sigungu, detail, " ".join(tokens)), "detail_parent")

    # 과거·통합 시군구가 첫 행정 토큰인 경우
    first_admin_index = 1 if tokens and tokens[0] in PROVINCE_ALIASES else 0
    if first_admin_index < len(tokens):
        historical = HISTORICAL_SIGUNGU_ALIASES.get(tokens[first_admin_index])
        if historical:
            sido, sigungu = historical
            detail = None
            if first_admin_index + 1 < len(tokens):
                detail = _collect_detail_tokens(tokens, first_admin_index + 1, source_text)
            if detail and (sido, sigungu, detail.split()[0]) in AMBIGUOUS_HISTORICAL_DETAILS:
                detail = None
            # 옛 마산 회원구는 현행 마산회원구
            if tokens[first_admin_index] == "마산시" and first_admin_index + 1 < len(tokens) and tokens[first_admin_index + 1] == "회원구":
                sigungu = "창원시 마산회원구"
                detail = _collect_detail_tokens(tokens, first_admin_index + 2, source_text) if first_admin_index + 2 < len(tokens) else None
            return LocationDecision(_parts(None, sido, sigungu or None, detail, " ".join(tokens)), "historical_alias")

    # 시도
    sido: str | None = None
    index = 0
    if tokens[0] in PROVINCE_ALIASES:
        sido = PROVINCE_ALIASES[tokens[0]]
        index = 1

    if index >= len(tokens):
        if sido:
            return LocationDecision(_parts(None, sido, None, None, " ".join(tokens)), "province_only")
        return None

    # 시군구가 생략됐지만 상세 읍면동으로 상위 지역이 유일하게 확정되는 경우.
    if tokens[index] in DETAIL_TO_PARENT:
        parent_sido, parent_sigungu = DETAIL_TO_PARENT[tokens[index]]
        if sido is None or sido == parent_sido:
            detail = _collect_detail_tokens(tokens, index, source_text)
            return LocationDecision(_parts(None, parent_sido, parent_sigungu, detail, " ".join(tokens)), "detail_parent")

    # 세종특별자치시는 시군구 없이 읍면동이 바로 올 수 있다.
    if sido == "세종특별자치시" and tokens[index].endswith(("읍", "면", "동", "리")):
        detail = _collect_detail_tokens(tokens, index, source_text)
        return LocationDecision(_parts(None, sido, None, detail, " ".join(tokens)), "resolved")

    # 구만 적힌 서울 자료
    if tokens[index] in SEOUL_DISTRICTS and sido is None:
        sido = "서울특별시"

    if sido is None and tokens[index] in DETAIL_TO_PARENT:
        parent_sido, parent_sigungu = DETAIL_TO_PARENT[tokens[index]]
        detail = _collect_detail_tokens(tokens, index, source_text)
        return LocationDecision(_parts(None, parent_sido, parent_sigungu, detail, " ".join(tokens)), "detail_parent")

    # 부산 `진구` 반복 표기
    if sido == "부산광역시" and tokens[index] == "진구":
        tokens[index] = "부산진구"

    # 옛 인천 남구는 동명이 있을 때만 현행 구로 구분
    if sido == "인천광역시" and tokens[index] == "남구":
        detail_candidate = tokens[index + 1] if index + 1 < len(tokens) else None
        district = INCHEON_OLD_NAMGU_DETAIL_TO_DISTRICT.get(detail_candidate or "")
        if district:
            return LocationDecision(_parts(None, sido, district, detail_candidate, " ".join(tokens)), "historical_alias")
        return LocationDecision(_parts(None, sido, None, None, " ".join(tokens)), "ambiguous_old_district", reason="옛 인천 남구는 현행 구를 단정할 수 없음")
    if sido == "인천광역시" and tokens[index] == "북구":
        return LocationDecision(_parts(None, sido, None, None, " ".join(tokens)), "ambiguous_old_district", reason="옛 인천 북구는 현행 구를 단정할 수 없음")

    admin = tokens[index]
    # `경주`, `남양주`, `포항`처럼 접미사가 생략된 유일 도시명
    if not admin.endswith(("시", "군", "구")):
        candidates = [name for name in SIGUNGU_TO_PROVINCES if name[:-1] == admin]
        if sido:
            candidates = [name for name in candidates if name in PROVINCE_SIGUNGU.get(sido, set())]
        if len(candidates) == 1:
            admin = candidates[0]
        elif admin == "수원":
            admin = "수원시"
        elif admin == "남양주":
            admin = "남양주시"
        elif admin == "포항":
            admin = "포항시"
        elif admin == "경주":
            admin = "경주시"
        elif admin == "공주":
            admin = "공주시"
        else:
            return None

    # 시도 없는 시군구는 역색인으로 유일한 상위 시도만 보완
    if sido is None:
        provinces = SIGUNGU_TO_PROVINCES.get(admin, set())
        if len(provinces) == 1:
            sido = next(iter(provinces))
        elif admin == "광주시":
            # 읍·면이 뒤따르면 경기도 광주시, 상무지구면 광주광역시
            tail = " ".join(tokens[index + 1:])
            if re.search(r"(?:읍|면)(?:\s|$)", tail):
                sido = "경기도"
            elif "상무지구" in source_text:
                sido = "광주광역시"
                admin = "서구"
            else:
                return None
        else:
            return None

    # 시+구 조합
    sigungu = admin
    next_index = index + 1
    if admin.endswith("시") and next_index < len(tokens) and tokens[next_index].endswith("구"):
        district = tokens[next_index]
        if district in CITY_DISTRICTS.get((sido, admin), set()):
            sigungu = f"{admin} {district}"
            next_index += 1
        else:
            # 원문 도시명이 오타이고 구 이름은 같은 시도에서 유일하게 유효한 경우 도시를 보정한다.
            owners = [
                city_name
                for (province_name, city_name), districts in CITY_DISTRICTS.items()
                if province_name == sido and district in districts
            ]
            if len(owners) == 1:
                admin = owners[0]
                sigungu = f"{admin} {district}"
                next_index += 1

    # 시도/시군구 조합 검증. 원문 시도가 틀렸어도 시군구의 유일한 실제 시도를 우선한다.
    city = sigungu.split()[0]
    valid_province = city in PROVINCE_SIGUNGU.get(sido, set())
    if not valid_province:
        actual_provinces = SIGUNGU_TO_PROVINCES.get(city, set())
        if len(actual_provinces) == 1:
            sido = next(iter(actual_provinces))
        else:
            return None

    detail = _collect_detail_tokens(tokens, next_index, source_text) if next_index < len(tokens) else None
    if detail and (sido, sigungu, detail.split()[0]) in AMBIGUOUS_HISTORICAL_DETAILS:
        detail = None
    return LocationDecision(_parts(None, sido, sigungu, detail, " ".join(tokens)), "resolved")


def _split_compact_location(value: str) -> str:
    """`충남예산군`, `인천시서구원창동`처럼 붙은 행정구역만 안전하게 분리한다."""
    text = value.strip()
    first_token = text.split()[0] if text.split() else ""
    # `제주시`, `광주시`처럼 시도 약칭으로 시작하지만 그 자체가 시군구인 값은 분리하지 않는다.
    if first_token in SIGUNGU_TO_PROVINCES or first_token in HISTORICAL_SIGUNGU_ALIASES:
        return text
    for alias in sorted(PROVINCE_ALIASES, key=len, reverse=True):
        if not text.startswith(alias):
            continue
        if len(text) == len(alias) or text[len(alias)].isspace():
            return text
        sido = PROVINCE_ALIASES[alias]
        rest = text[len(alias):].lstrip(" ,.;:：()[]{}")
        # 시도 뒤 현행 시군구가 붙은 경우 가장 긴 이름을 우선한다.
        for admin in sorted(PROVINCE_SIGUNGU.get(sido, set()), key=len, reverse=True):
            if rest.startswith(admin):
                tail = rest[len(admin):].strip()
                return " ".join(part for part in (alias, admin, tail) if part)
        # 과거·오타 시군구도 같은 방식으로 분리한다.
        for admin in sorted(set(HISTORICAL_SIGUNGU_ALIASES) | set(ADMIN_TYPO_ALIASES), key=len, reverse=True):
            if rest.startswith(admin):
                tail = rest[len(admin):].strip()
                return " ".join(part for part in (alias, admin, tail) if part)
        return f"{alias} {rest}"
    return text


def parse_location_relaxed(raw_value: Any, source_text: str = "") -> LocationDecision:
    raw = _clean_location_value(str(raw_value or ""))
    if not raw:
        return LocationDecision(LocationParts(None, None, None, None, False, None), "empty")

    # 명백히 위치가 아닌 값
    if re.fullmatch(r"\(?주\)?\s*[○OX0]+.*", raw, re.I) or raw.startswith("(주)"):
        return LocationDecision(LocationParts(None, None, None, None, False, raw), "not_location")

    if raw.replace(" ", "") == "전북이리":
        return LocationDecision(_parts(None, "전북특별자치도", "익산시", None, raw), "historical_alias")
    # 광역시 승격 전 `경남 울산시` 표기는 현행 울산광역시로 정규화한다.
    if re.match(r"^경남\s+울산시(?:\s|$)", raw):
        raw = re.sub(r"^경남\s+울산시", "울산광역시", raw)
    if re.match(r"^충남\s+대전시(?:\s|$)", raw):
        raw = re.sub(r"^충남\s+대전시", "대전광역시", raw)

    if re.search(r"(?:경남\s+)?울산시\s+울주(?:구|군)", raw):
        detail_match = re.search(r"(\S+(?:읍|면|동|리))", raw.split("울주", 1)[-1])
        detail = ADMIN_TYPO_ALIASES.get(detail_match.group(1), detail_match.group(1)) if detail_match else None
        return LocationDecision(_parts(None, "울산광역시", "울주군", detail, raw), "historical_alias")
    if "광주시 상무지구" in raw or "광주 상무지구" in raw:
        return LocationDecision(_parts(None, "광주광역시", "서구", None, raw), "named_area")

    for name, (sido, sigungu, detail) in NAMED_AREA_ALIASES.items():
        if name in raw:
            return LocationDecision(_parts(None, sido, sigungu, detail, raw), "named_area")
    if "시화지구" in raw:
        return LocationDecision(LocationParts(None, None, None, None, False, raw), "ambiguous_named_area", reason="시화지구는 여러 시에 걸침")

    # 반복 오타를 문자열 단위로 보정
    normalized = _split_compact_location(raw)
    for wrong, right in sorted(ADMIN_TYPO_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True):
        normalized = re.sub(rf"(?<![가-힣0-9]){re.escape(wrong)}(?![가-힣0-9])", right, normalized)
    normalized = normalized.replace("광주군·읍", "광주군 광주읍")
    normalized = re.sub(r"(암사|목|방배|홍제)\s+(\d+동)", r"\1\2", normalized)

    tokens = [t.strip(" ,.;:：()[]{}") for t in normalized.split() if t.strip(" ,.;:：()[]{}")]
    decision = _current_parts_from_tokens(tokens, source_text or normalized)
    if decision:
        return decision
    # 시·도 다음에 시군구 없이 서로 다른 읍면동 토큰만 이어지는 값은
    # 원문 계층 오류로 분류하고 추측하지 않는다.
    if (
        tokens
        and tokens[0] in PROVINCE_ALIASES
        and len(tokens) >= 3
        and all(token.endswith(("읍", "면", "동", "리")) for token in tokens[1:])
    ):
        return LocationDecision(
            LocationParts(None, None, None, None, False, raw),
            "ambiguous_invalid_hierarchy",
            raw,
            "시군구가 없고 하위 행정구역 계층이 충돌함",
        )
    if tokens and tokens[0] in PROVINCE_ALIASES:
        sido = PROVINCE_ALIASES[tokens[0]]
        # 다른 시도의 유효 시군구가 붙은 경우에는 어느 쪽도 단정하지 않는다.
        if len(tokens) >= 2 and tokens[1].endswith(("시", "군", "구")):
            owners = SIGUNGU_TO_PROVINCES.get(ADMIN_TYPO_ALIASES.get(tokens[1], tokens[1]), set())
            if owners and sido not in owners:
                return LocationDecision(
                    LocationParts(None, None, None, None, False, raw),
                    "invalid_province_admin_conflict",
                    raw,
                    "시도와 시군구가 서로 일치하지 않음",
                )
        # 마스킹·불명확한 단일 하위 지명은 확실한 시도까지만 보존한다.
        if len(tokens) == 2 or any(re.search(r"[○OX0]{2,}|일대", token, re.I) for token in tokens[1:]):
            return LocationDecision(_parts(None, sido, None, None, raw), "province_only_ambiguous_detail")
    # 괄호가 닫히지 않은 값처럼 원문 형식이 깨졌지만 시도만 확실한 경우.
    for alias in sorted(PROVINCE_ALIASES, key=len, reverse=True):
        if raw.startswith(alias):
            return LocationDecision(_parts(None, PROVINCE_ALIASES[alias], None, None, raw), "province_only_malformed")
    return LocationDecision(LocationParts(None, None, None, None, False, raw), "unresolved", raw, "행정구역을 안전하게 확정할 수 없음")


def _candidate_context_score(content: str, start: int, end: int) -> int:
    before = content[max(0, start - 100):start]
    after = content[end:end + 160]
    score = 0
    if SITE_EVIDENCE_RE.search(after):
        score += 20
    if re.search(r"(?:사고|재해).{0,50}(?:발생|사망|부상)", after):
        score += 10

    # 소속업체 표현이 후보 바로 앞의 같은 절에 있을 때만 감점한다.
    affiliation_positions = [before.rfind(token) for token in ("소속업체", "소속 업체", "본사", "업체 주소", "사업주 주소")]
    affiliation_pos = max(affiliation_positions)
    site_positions = [before.rfind(token) for token in ("사고는", "사고가", "재해는", "재해가", "발생지는", "발생 장소는")]
    site_pos = max(site_positions)
    if affiliation_pos >= 0 and affiliation_pos > site_pos and len(before) - affiliation_pos <= 55:
        score -= 45
    if AFFILIATION_AFTER_RE.search(after):
        score -= 35
    return score


def _location_candidates_from_text(content_text: str | None) -> list[tuple[int, int, LocationDecision, str]]:
    if not content_text:
        return []
    content = normalize_text(content_text[:3000])
    candidates: list[tuple[int, int, LocationDecision, str]] = []

    # 명시 라벨은 가장 강한 근거
    for raw in extract_labeled_location_raw(content):
        decision = parse_location_relaxed(raw, content)
        if decision.parts.valid:
            candidates.append((100 + decision.parts.specificity, 0, decision, "label"))

    # 시도 포함 또는 시군구 단독 후보
    patterns = [
        # `경기 광주`는 광주광역시가 아니라 경기도 광주시를 뜻한다.
        re.compile(r"(?<![가-힣])(?P<loc>경기(?:도)?\s+광주(?:시)?(?:\s+[가-힣0-9]{1,16}(?:읍|면|동|리))?)"),
        re.compile(rf"(?<![가-힣])(?P<loc>(?:{PROVINCE_ALT}))(?=\s+소재(?:\s|[○OX0]{{2,}}|[가-힣0-9]+(?:조선소|공장|사업장|현장)))"),
        re.compile(rf"(?<![가-힣])(?P<loc>(?:{PROVINCE_ALT})\s+[가-힣0-9]{{1,16}}(?:시|군|구)(?:\s+[가-힣0-9]{{1,16}}(?:구|읍|면|동|리)){{0,2}})"),
        # `강원 고성`, `경남 고성`처럼 시군 접미사가 생략된 표기
        re.compile(rf"(?<![가-힣])(?P<loc>(?:{PROVINCE_ALT})\s+[가-힣]{{2,12}}(?:\s+[가-힣0-9]{{1,16}}(?:읍|면|동|리))?)(?=\s+(?:소재|내|현장|사업장|공장|작업장|철거|중공업))"),
        re.compile(r"(?<![가-힣])(?P<loc>[가-힣]{2,12}(?:시|군)\s+[가-힣0-9]{1,16}(?:구|읍|면|동|리)(?:\s+[가-힣0-9]{1,16}(?:읍|면|동|리))?)"),
        re.compile(r"(?<![가-힣])(?P<loc>[가-힣]{2,12}(?:시|군|구))(?![가-힣])"),
    ]
    seen: set[tuple[int, str]] = set()
    covered_spans: list[tuple[int, int]] = []
    for pattern_index, pattern in enumerate(patterns):
        for match in pattern.finditer(content):
            if pattern_index > 2 and any(start <= match.start() and match.end() <= end for start, end in covered_spans):
                continue
            raw = match.group("loc")
            decision = parse_location_relaxed(raw, content)
            if not decision.parts.valid:
                continue
            key = (match.start(), decision.parts.location or "")
            if key in seen:
                continue
            seen.add(key)
            score = 20 + decision.parts.specificity + _candidate_context_score(content, match.start(), match.end())
            candidates.append((score, match.start(), decision, "narrative"))
            if pattern_index in (0, 1, 2):
                covered_spans.append((match.start(), match.end()))
    return candidates


def _select_best_body_location(content_text: str | None) -> LocationDecision:
    candidates = _location_candidates_from_text(content_text)
    if not candidates:
        return LocationDecision(LocationParts(None, None, None, None, False, None), "none")
    candidates.sort(key=lambda row: (row[0], row[2].parts.specificity, -row[1]), reverse=True)
    best = candidates[0]
    if best[0] < 20:
        return LocationDecision(LocationParts(None, None, None, None, False, None), "none", reason="사고지 근거 점수가 부족함")
    # 같은 점수의 서로 다른 지역이면 추측하지 않는다.
    for other in candidates[1:]:
        if other[0] != best[0]:
            break
        if other[2].parts.location != best[2].parts.location:
            return LocationDecision(LocationParts(None, None, None, None, False, None), "conflict", reason="동일 점수 위치 충돌")
    return LocationDecision(best[2].parts, best[3])


def _same_hierarchy(left: LocationParts, right: LocationParts) -> bool:
    if not left.valid or not right.valid:
        return False
    if left.sido and right.sido and left.sido != right.sido:
        return False
    left_tokens = (left.sigungu or "").split()
    right_tokens = (right.sigungu or "").split()
    left_city = left_tokens[0] if left_tokens else ""
    right_city = right_tokens[0] if right_tokens else ""
    return not (left_city and right_city and left_city != right_city)


def _apply_location(row: dict[str, Any], decision: LocationDecision) -> None:
    parts = decision.parts
    row["location"] = parts.location if parts.valid else None
    row["location_sido"] = parts.sido if parts.valid else None
    row["location_sigungu"] = parts.sigungu if parts.valid else None
    row["location_detail"] = parts.detail if parts.valid else None


def _has_ambiguous_route(content_text: str | None) -> bool:
    if not content_text or not ROUTE_RE.search(content_text):
        return False
    candidates = _location_candidates_from_text(content_text)
    cities = {
        (row[2].parts.sido, (row[2].parts.sigungu or "").split()[0] if row[2].parts.sigungu else None)
        for row in candidates
        if row[2].parts.valid
    }
    return len(cities) >= 2


def _existing_location_is_affiliation(content_text: str | None, parts: LocationParts) -> bool:
    if not content_text or not parts.valid:
        return False
    anchors = [parts.detail, (parts.sigungu or "").split()[-1] if parts.sigungu else None, parts.location]
    for anchor in [value for value in anchors if value]:
        for match in re.finditer(re.escape(anchor), content_text):
            after = content_text[match.end():match.end() + 120]
            before = content_text[max(0, match.start() - 60):match.start()]
            if AFFILIATION_AFTER_RE.search(after) or AFFILIATION_BEFORE_RE.search(before):
                return True
    return False


def postprocess_domestic_row(base_row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = dict(base_row)
    row = dict(base_row)
    row["content_text"] = clean_content_text_final(row.get("content_text"))

    if NON_ACCIDENT_TITLE_RE.search(str(row.get("title") or "")):
        decision = LocationDecision(LocationParts(None, None, None, None, False, None), "non_accident_document")
    elif _has_ambiguous_route(row.get("content_text")):
        # 서로 다른 두 행정구역을 잇는 도로·관로 구간에서 정확한 지점이 없으면 단정하지 않는다.
        decision = LocationDecision(LocationParts(None, None, None, None, False, None), "route_ambiguous")
    else:
        candidate = _select_best_body_location(row.get("content_text"))
        labeled_raw = extract_labeled_location_raw(row.get("content_text"))
        labeled_decisions = [parse_location_relaxed(raw, row.get("content_text") or "") for raw in labeled_raw]
        # 명시 라벨 값이 비정상인데 서술형 탐색이 시·도 하나만 건진 경우에는
        # 그 시·도를 사고지로 단정하지 않는다. 더 구체적인 별도 사고지 근거는 허용한다.
        if labeled_raw and not any(item.parts.valid for item in labeled_decisions) and candidate.parts.specificity <= 1:
            candidate = LocationDecision(
                LocationParts(None, None, None, None, False, None),
                "invalid_explicit_location",
                reason="명시 위치 라벨의 행정구역 계층이 불명확함",
            )
        existing = parse_location_relaxed(row.get("location"), row.get("content_text") or "")
        if existing.parts.valid:
            # 기존 정상 위치는 고정한다. 소속업체 주소로 확인된 경우를 먼저 교체하고,
            # 그 외에는 같은 지역의 더 구체적인 값만 보완한다.
            if candidate.parts.valid and _existing_location_is_affiliation(row.get("content_text"), existing.parts):
                decision = LocationDecision(candidate.parts, "accident_site_over_affiliation")
            elif candidate.parts.valid and _same_hierarchy(existing.parts, candidate.parts):
                decision = candidate if candidate.parts.specificity > existing.parts.specificity else LocationDecision(existing.parts, "base_preserved")
            else:
                decision = LocationDecision(existing.parts, "base_preserved")
        else:
            decision = candidate
    _apply_location(row, decision)

    protected = set(row) - ALLOWED_POSTPROCESS_COLUMNS
    changed_protected = [key for key in protected if row.get(key) != before.get(key)]
    if changed_protected:
        raise RuntimeError(f"domestic 후처리가 보호 컬럼을 변경했습니다: {changed_protected}")
    return row, {"location_status": decision.status, "location_reason": decision.reason}


def _title_location_decision(title_raw: str | None, content_text: str | None) -> LocationDecision:
    _title, _date, raw_location, _raw = parse_fatal_title(title_raw)
    if not raw_location:
        return LocationDecision(LocationParts(None, None, None, None, False, None), "none")
    # `경기광주`처럼 붙은 표기
    raw_location = re.sub(r"^경기\s*광주(?:시)?$", "경기도 광주시", raw_location.strip())
    return parse_location_relaxed(raw_location, raw_location)


def _one_character_difference(left: str, right: str) -> bool:
    if len(left) != len(right):
        return False
    return sum(a != b for a, b in zip(left, right)) == 1


def postprocess_fatal_row(base_row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before = dict(base_row)
    row = dict(base_row)
    row["content_text"] = clean_content_text_final(row.get("content_text"))

    title = _title_location_decision(row.get("title_raw"), row.get("content_text"))
    body = _select_best_body_location(row.get("content_text"))

    if title.parts.valid and body.parts.valid:
        if _same_hierarchy(title.parts, body.parts):
            title_detail = title.parts.detail
            body_detail = body.parts.detail
            if title_detail and body_detail and title_detail != body_detail:
                # 제목의 `영동`과 본문의 `영동읍`처럼 같은 지명의 축약형이면
                # 행정단위 접미사가 명확한 본문 값을 사용한다. 서로 다른 상세 지역이면 충돌이다.
                title_compact = title_detail.replace(" ", "")
                body_compact = body_detail.replace(" ", "")
                if (
                    body_detail.startswith(title_detail + " ")
                    or title_detail.startswith(body_detail + " ")
                    or body_compact[:-1] == title_compact
                    or title_compact[:-1] == body_compact
                ):
                    selected = body if body.parts.specificity >= title.parts.specificity else title
                    decision = LocationDecision(selected.parts, "consistent_detail_normalized")
                elif (
                    title_compact[-1:] == body_compact[-1:]
                    and _one_character_difference(title_compact, body_compact)
                ):
                    # 같은 시군구 안의 한 글자 제목 오타는 현장 문맥이 있는 본문 값을 사용한다.
                    decision = LocationDecision(body.parts, "title_detail_typo_corrected")
                else:
                    decision = LocationDecision(LocationParts(None, None, None, None, False, None), "true_detail_conflict")
            else:
                selected = body if body.parts.specificity > title.parts.specificity else title
                decision = LocationDecision(selected.parts, "consistent")
        else:
            # 본문 후보가 명시적인 사고 현장이고 제목은 오타/비표준이면 본문 사용.
            # 둘 다 서로 다른 유효 지역이면 기존 요구대로 NULL.
            decision = LocationDecision(LocationParts(None, None, None, None, False, None), "true_conflict")
    elif body.parts.valid:
        decision = LocationDecision(body.parts, "body")
    elif title.parts.valid:
        decision = LocationDecision(title.parts, "title")
    else:
        # 기존값이 유효하면 회귀 방지를 위해 보존
        existing = parse_location_relaxed(row.get("location"), "\n".join(filter(None, [row.get("title_raw"), row.get("content_text")])))
        decision = LocationDecision(existing.parts, "base_preserved") if existing.parts.valid else LocationDecision(LocationParts(None, None, None, None, False, None), "none")

    _apply_location(row, decision)
    protected = set(row) - ALLOWED_POSTPROCESS_COLUMNS
    changed_protected = [key for key in protected if row.get(key) != before.get(key)]
    if changed_protected:
        raise RuntimeError(f"fatal 후처리가 보호 컬럼을 변경했습니다: {changed_protected}")
    return row, {"location_status": decision.status, "location_reason": decision.reason}


def postprocess_rows(
    domestic_rows: list[dict[str, Any]],
    fatal_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    domestic_out: list[dict[str, Any]] = []
    fatal_out: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    unresolved: list[dict[str, Any]] = []

    for row in domestic_rows:
        fixed, meta = postprocess_domestic_row(row)
        domestic_out.append(fixed)
        key = f"domestic:{meta['location_status']}"
        status_counts[key] = status_counts.get(key, 0) + 1
        if meta["location_status"] == "unresolved":
            unresolved.append({"table": "domestic_cases", "id": row.get("id"), "reason": meta.get("location_reason")})

    for row in fatal_rows:
        fixed, meta = postprocess_fatal_row(row)
        fatal_out.append(fixed)
        key = f"fatal:{meta['location_status']}"
        status_counts[key] = status_counts.get(key, 0) + 1
        if meta["location_status"] == "unresolved":
            unresolved.append({"table": "fatal_cases", "id": row.get("id"), "reason": meta.get("location_reason")})

    return domestic_out, fatal_out, {"status_counts": status_counts, "unresolved": unresolved}
