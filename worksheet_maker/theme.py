"""학습지 스타일(테마).

스타일은 자유로운 CSS가 아니라 '정해진 선택지의 조합'이다. AI가 참고 학습지를 보고 스타일을 고를 때도
이 선택지 안에서만 고르므로, 어떤 스타일이든 인쇄 규격과 일관성이 깨지지 않는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ValidationInfo, field_validator

THEME_DIR = Path(__file__).resolve().parent.parent / "themes"

# 항목: (한국어 이름, {값: 설명}) — 첫 번째 값이 기본값
THEME_OPTIONS: dict[str, tuple[str, dict[str, str]]] = {
    "header": ("머리말 모양", {
        "rule": "위 굵은 선 + 아래 가는 선",
        "box": "테두리 상자",
        "band": "검은 띠 위에 흰 제목",
        "center": "가운데 정렬 + 이중선",
        "nameplate": "둥근 이름표 (저학년)",
        "exam": "시험지처럼 칸 나뉜 머리말",
    }),
    "section": ("소제목 모양", {
        "underline": "아래 밑줄",
        "bar": "왼쪽 굵은 막대",
        "box": "테두리 상자",
        "band": "검은 띠 위에 흰 글씨",
        "pill": "둥근 알약 모양",
        "number": "큰 번호 배지 + 밑줄",
    }),
    "qnum": ("문항 번호", {
        "plain": "1. 2. 3.",
        "circle": "원 안의 숫자",
        "box": "네모 안의 숫자",
        "filled": "검은 원에 흰 숫자",
    }),
    "tag": ("라벨(학습 목표·읽기 자료 등)", {
        "outline": "테두리만",
        "filled": "검정 바탕 흰 글씨",
    }),
    "heading_font": ("제목 글꼴", {"sans": "고딕", "serif": "명조"}),
    "body_font": ("본문 글꼴", {"sans": "고딕", "serif": "명조"}),
    "line": ("선 굵기", {"normal": "보통", "thin": "가늘게", "bold": "굵게"}),
    "corner": ("모서리", {"round": "살짝 둥글게", "square": "각지게", "big": "아주 둥글게"}),
    "shade": ("상자 배경", {"none": "없음 (흰색)", "light": "옅은 회색"}),
    "density": ("간격", {"normal": "보통", "compact": "촘촘하게", "airy": "여유 있게"}),
    "frame": ("쪽 테두리", {"none": "없음", "box": "네모 테두리", "rounded": "둥근 테두리", "notebook": "공책 여백선"}),
    "qstyle": ("문항 꾸밈", {"plain": "꾸밈 없음", "divided": "문항 사이 구분선", "boxed": "문항마다 상자", "card": "둥근 회색 카드"}),
    "deco": ("소제목 아이콘", {"none": "없음", "icons": "✏️ 📘 🔍 ⭐ 아이콘"}),
    "size": ("글자 크기", {"normal": "보통", "large": "크게 (저학년·읽기 편하게)"}),
}


class Theme(BaseModel):
    name: str = "기본"
    header: str = "rule"
    section: str = "underline"
    qnum: str = "plain"
    tag: str = "outline"
    heading_font: str = "sans"
    body_font: str = "sans"
    line: str = "normal"
    corner: str = "round"
    shade: str = "none"
    density: str = "normal"
    frame: str = "none"
    qstyle: str = "plain"
    deco: str = "none"
    size: str = "normal"

    @field_validator(*THEME_OPTIONS, mode="before")
    @classmethod
    def _allowed(cls, v: object, info: ValidationInfo) -> str:
        # 선택지에 없는 값은 기본값으로 되돌린다.
        choices = THEME_OPTIONS[info.field_name][1]
        return v if v in choices else next(iter(choices))


# 선 굵기(pt): 가는 선, 보통 선, 강조 선, 머리말 선
_LINES = {"thin": (0.4, 0.6, 1.0, 1.5), "normal": (0.5, 0.75, 1.25, 2.25), "bold": (0.75, 1.1, 1.75, 3.0)}
_FONTS = {
    "sans": '"Noto Sans KR", "Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", sans-serif',
    "serif": '"Noto Serif KR", "Batang", "바탕", "AppleMyungjo", serif',
}
_DENSITY = {"compact": 0.75, "normal": 1.0, "airy": 1.3}


def css_vars(t: Theme) -> str:
    b1, b2, b3, b4 = _LINES[t.line]
    return (
        f"--b1: {b1}pt; --b2: {b2}pt; --b3: {b3}pt; --b4: {b4}pt; "
        f"--radius: {({'round': '1.5mm', 'big': '4mm'}).get(t.corner, '0')}; "
        f"--shade: {'#efefef' if t.shade == 'light' else 'transparent'}; "
        f"--gap: {_DENSITY[t.density]}; "
        f"--font-body: {_FONTS[t.body_font]}; --font-head: {_FONTS[t.heading_font]};"
    )


def body_classes(t: Theme) -> str:
    return f"hd-{t.header} sec-{t.section} qn-{t.qnum} tag-{t.tag} fr-{t.frame} qs-{t.qstyle} deco-{t.deco}"


def response_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            key: {"type": "string", "enum": list(choices), "description": label}
            for key, (label, choices) in THEME_OPTIONS.items()
        },
        "required": list(THEME_OPTIONS),
    }


def style_guide() -> str:
    return "\n".join(
        f"- {key} ({label}): " + ", ".join(f"{v}={desc}" for v, desc in choices.items())
        for key, (label, choices) in THEME_OPTIONS.items()
    )


# ----- 저장·불러오기 (themes/*.json) -----
def _path(name: str) -> Path:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "스타일"
    return THEME_DIR / f"{safe}.json"


PRESET_ORDER = ["기본", "교과서형", "활동지형", "시험지형", "저학년 놀이형", "노트형"]


def list_themes() -> list[Theme]:
    themes = [Theme()]
    for p in sorted(THEME_DIR.glob("*.json")):
        try:
            t = Theme.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if t.name != "기본":
            themes.append(t)
    # 기본 제공 모양을 앞에, 직접 저장한 모양은 뒤에
    order = {n: i for i, n in enumerate(PRESET_ORDER)}
    return sorted(themes, key=lambda t: order.get(t.name, len(order)))


def save_theme(t: Theme) -> Path:
    THEME_DIR.mkdir(exist_ok=True)
    p = _path(t.name)
    p.write_text(t.model_dump_json(indent=2), encoding="utf-8")
    return p
