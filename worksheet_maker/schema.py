"""학습지 데이터 구조.

AI(LLM)는 이 구조에 맞는 JSON만 만든다. 모양(HTML/CSS)은 render.py가 항상 같은 규칙으로 찍어낸다.
새 컴포넌트를 추가할 때는 BLOCK_TYPES에 한 줄을 넣고, templates/의 HTML·CSS에 모양을 정의한다.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from . import diagrams

# 컴포넌트 목록: type → (한국어 이름, AI에게 주는 설명, 사용하는 필드)
# 이 표가 AI 프롬프트, JSON 스키마, 앱의 도움말에 모두 쓰인다.
BLOCK_TYPES: dict[str, tuple[str, str, str]] = {
    "section": ("소제목", "학습지 안의 큰 구분(도입, 전개, 정리 등)", "title"),
    "text": ("설명 글", "일반 설명 문단", "title(선택), body"),
    "concept": ("핵심 개념", "꼭 알아야 할 용어·개념 정리. items는 '용어: 설명' 형식", "title(선택), items"),
    "reading": ("읽기 자료", "지문, 사료, 기사, 제시문 등 학생이 읽을 자료", "title, body, source(출처)"),
    "quote": ("인용문", "짧은 명언이나 인물의 발언", "body, source(말한 사람·출처)"),
    "table": ("표", "표 형태의 자료. rows의 첫 행이 머리글", "title(선택), rows"),
    "activity": ("활동", "모둠·개별 활동의 안내문과 진행 순서", "title, body, items(진행 순서)"),
    "short_answer": ("단답형", "짧게 답하는 문항", "body(질문), lines(답 줄 수, 기본 1)"),
    "essay": ("서술형", "문장으로 길게 답하는 문항", "body(질문), lines(답 줄 수, 기본 5)"),
    "multiple_choice": ("선택형", "보기 중에서 고르는 문항. 선택지에 번호를 붙이지 않는다", "body(질문), items(선택지)"),
    "fill_blank": ("빈칸 채우기", "문장 속 빈칸. 빈칸 자리는 [[정답]]으로 표시", "body"),
    "ox": ("O/X", "맞으면 O, 틀리면 X를 고르는 진술문 묶음", "title(선택), items(진술문)"),
    "drawing": ("그리기 공간", "학생이 그림, 마인드맵, 도식을 직접 그리는 빈 상자", "body(지시문), lines(높이, 기본 8)"),
    "diagram": (
        "도식",
        "수학 개념을 보여 주는 정확한 그림(묶음, 배열, 수직선, 분수, 시계, 그래프, 도형, 각도, 수 모형). "
        "프로그램이 diagram_json대로 정확히 그린다. body에 그림 설명",
        "body(그림 설명), diagram_json(아래 도식 형식의 JSON 문자열)",
    ),
    "image": (
        "그림",
        "학생에게 보여주는 장면 그림·삽화·사진·지도. 수학 도식으로 그릴 수 있는 것은 diagram을 쓴다. body에 어떤 그림인지 짧게 설명한다",
        "body(그림 설명), source(출처), 첨부 파일 속 그림이면 file·page·box(위치)",
    ),
    "reflection": ("스스로 점검", "학습을 마친 뒤 자기 평가 체크리스트", "title(선택), items"),
    "drill": (
        "연습 문제 묶음",
        "짧은 문제 여러 개를 칸에 촘촘히 늘어놓는 반복 연습. items의 각 문제 안 정답 자리를 [[정답]]으로 표시한다"
        "(예: '37 + 48 = [[85]]', 'apple → [[사과]]')",
        "title(지시문), items(문제 8~16개)",
    ),
    "cornell": (
        "정리 노트",
        "왼쪽 핵심어, 오른쪽 설명의 코넬식 정리 노트. items는 '핵심어: 설명' 형식이고 설명 속 중요한 낱말을 [[정답]] 빈칸으로",
        "title(소주제), items(3~6개)",
    ),
    "bingo": (
        "빙고판",
        "학습지의 문항 정답으로 프로그램이 빙고판을 만든다. items에는 정답과 헷갈리는 오답(그럴듯한 값)을 8개 이상 쓴다",
        "title(선택), items(오답 후보)",
    ),
}

# 자동 번호(1, 2, 3 …)가 붙는 문항 컴포넌트
QUESTION_TYPES = {"short_answer", "essay", "multiple_choice", "fill_blank", "ox", "drawing", "drill", "cornell"}

DEFAULT_LINES = {"short_answer": 1, "essay": 5, "drawing": 8}

# 그림 크기: 값 → (한국어 이름, 본문 폭 대비 비율)
IMAGE_SIZES = {"m": ("보통", 60), "s": ("작게", 40), "l": ("크게", 80), "full": ("가득", 100)}
# 그림은 HTML 안에 넣는 data URI만 허용한다 (외부 주소·스크립트가 섞이지 않게).
_IMAGE_URI = re.compile(r"data:image/(?:png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+=*")


class Block(BaseModel):
    type: str
    title: str = ""
    body: str = ""
    items: list[str] = Field(default_factory=list)
    source: str = ""
    rows: list[list[str]] = Field(default_factory=list)
    lines: int = 0
    image: str = ""  # 그림 구성요소의 그림 (data URI). AI는 채우지 않는다.
    size: str = "m"  # 그림 크기 (IMAGE_SIZES)
    answer: str = ""  # 문항의 정답 (정답·해설 쪽에 표시). 빈칸 채우기는 [[정답]]에서 자동으로 뽑는다.
    solution: str = ""  # 해설: 정답인 까닭을 1~2문장으로
    diagram: dict = Field(default_factory=dict)  # 도식 설계도 (diagrams.KINDS). 그림은 프로그램이 그린다.

    @model_validator(mode="before")
    @classmethod
    def _diagram_from_ai(cls, data):
        # AI는 diagram_json(문자열)으로 준다 → 설계도(dict)로 바꾼다.
        if isinstance(data, dict) and not data.get("diagram") and data.get("diagram_json"):
            data = {**data, "diagram": data["diagram_json"]}
        return data

    @field_validator("diagram", mode="before")
    @classmethod
    def _safe_diagram(cls, v: object) -> dict:
        return diagrams.normalize(v) if v else {}

    @field_validator("type", mode="before")
    @classmethod
    def _known_type(cls, v: object) -> str:
        # 목록에 없는 type이 오면 레이아웃이 깨지지 않도록 설명 글로 처리한다.
        v = str(v or "").strip()
        return v if v in BLOCK_TYPES else "text"

    @field_validator("lines", mode="before")
    @classmethod
    def _clamp_lines(cls, v: object) -> int:
        try:
            return max(0, min(int(v or 0), 20))
        except (TypeError, ValueError):
            return 0

    @field_validator("image", mode="before")
    @classmethod
    def _safe_image(cls, v: object) -> str:
        v = str(v or "").strip()
        return v if _IMAGE_URI.fullmatch(v) else ""

    @field_validator("size", mode="before")
    @classmethod
    def _known_size(cls, v: object) -> str:
        return v if v in IMAGE_SIZES else "m"

    def answer_lines(self) -> int:
        return self.lines or DEFAULT_LINES.get(self.type, 1)

    def answer_text(self) -> str:
        """정답·해설 쪽에 보일 정답. 빈칸 채우기는 본문의 [[정답]]을 차례로 모은다."""
        if self.type == "fill_blank" and not self.answer:
            found = re.findall(r"\[\[(.*?)\]\]", self.body)
            return ", ".join(f"({i}) {a}" for i, a in enumerate(found, 1)) if len(found) > 1 else "".join(found)
        if self.type in ("drill", "cornell") and not self.answer:  # 문제(항목)마다 빈칸 정답을 모은다
            parts = [", ".join(re.findall(r"\[\[(.*?)\]\]", it)) for it in self.items]
            return "  ".join(f"({i}) {a}" for i, a in enumerate(parts, 1) if a)
        return self.answer


class Worksheet(BaseModel):
    kind: str = "standard"  # 학습지 유형 (kinds.KINDS): 짜임새와 배치. AI가 아니라 사용자가 고른다.
    subject: str = ""  # 과목
    unit: str = ""  # 단원
    lesson: str = ""  # 차시 (예: "3차시")
    title: str = "학습지"
    goals: list[str] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, v: object) -> str:
        from .kinds import KINDS

        return v if v in KINDS else "standard"


def response_schema() -> dict:
    """Gemini 구조화 출력용 JSON 스키마. 모델 호환성을 위해 $ref/anyOf 없이 평평하게 둔다."""
    string = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "과목명. 모르면 빈 문자열"},
            "unit": {"type": "string", "description": "단원명. 모르면 빈 문자열"},
            "lesson": {"type": "string", "description": "차시 (예: 3차시). 모르면 빈 문자열"},
            "title": {"type": "string", "description": "학습지 제목"},
            "goals": {"type": "array", "items": string, "description": "학습 목표"},
            "blocks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(BLOCK_TYPES)},
                        "title": string,
                        "body": string,
                        "items": {"type": "array", "items": string},
                        "source": string,
                        "rows": {"type": "array", "items": {"type": "array", "items": string}},
                        "lines": {"type": "integer"},
                        "answer": {"type": "string", "description": "문항의 정답 (요청받았을 때만)"},
                        "solution": {"type": "string", "description": "문항의 해설 1~2문장 (요청받았을 때만)"},
                        "diagram_json": {"type": "string", "description": "diagram일 때만: 도식 설계도 JSON 문자열. 아니면 빈 문자열"},
                        # image 전용: 첨부 파일 속 그림의 위치. 프로그램이 이 영역을 잘라 그림으로 넣는다.
                        "file": {"type": "integer", "description": "image: 몇 번째 첨부 파일인지 (1부터)"},
                        "page": {"type": "integer", "description": "image: PDF의 쪽 번호 (1부터). 이미지 파일은 1"},
                        "box": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": "image: [ymin, xmin, ymax, xmax], 그 쪽 전체를 0~1000으로 본 좌표",
                        },
                    },
                    # 선택 항목으로 두면 가벼운 모델이 내용·정답을 통째로 빼먹는다. 해당 없는 칸은 빈 값으로 받는다.
                    "required": ["type", "title", "body", "items", "rows", "answer", "solution", "diagram_json"],
                },
            },
        },
        "required": ["title", "blocks"],
    }


def block_schema() -> dict:
    """구성요소 하나의 JSON 스키마 (고치기 'AI 도움'에 쓴다)."""
    return response_schema()["properties"]["blocks"]["items"]


def component_guide() -> str:
    """AI 프롬프트와 앱 도움말에 넣는 컴포넌트 설명표 (도식 형식 포함)."""
    lines = "\n".join(
        f"- {key} ({name}): {desc}. 사용 필드: {fields}"
        for key, (name, desc, fields) in BLOCK_TYPES.items()
    )
    return (
        lines
        + "\n\n### 수와 식 쓰기\n- 분수는 2/6처럼, 대분수는 '1 2/3'처럼 쓴다. LaTeX(\\frac, $…$)는 쓰지 않는다. "
        "곱셈·나눗셈은 ×, ÷ 기호로 쓴다. 날짜는 '3월 1일'처럼 쓴다(3/1은 분수로 보인다)."
        + "\n\n### diagram_json 형식 (kind와 아래 값들. 예: {\"kind\": \"groups\", \"groups\": 16, \"per_group\": 4})\n"
        + diagrams.guide()
    )
