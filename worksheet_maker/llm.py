"""자료 → 학습지 JSON(+스타일). AI(Gemini)는 '의미 구조 분석'과 '스타일 선택지 고르기'만 하고 CSS는 만들지 않는다."""

from __future__ import annotations

import base64
import json
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import ValidationError

from . import theme as theme_mod
from . import curriculum
from . import kinds as kinds_mod
from .schema import BLOCK_TYPES, Worksheet, component_guide, response_schema
from .theme import Theme

# 첫 번째가 기본. 서버가 붐비거나 제한 시간을 넘기면 다음 모델로 바로 넘어간다.
# 2026-10 실측(3학년 수학 8문항): 3.5-flash-lite+생각 많이 22초·정답 모두 맞음 / 3.6-flash 50초 /
# 3.8·3.7-flash는 붐빌 때 거절(503)까지 2~3분이 걸렸고, 무료 키는 3.8-flash가 하루 20회로 제한된다.
MODELS = ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.6-flash"]
MODEL_LABELS = {
    "gemini-3.5-flash-lite": "빠름 · gemini-3.5-flash-lite (추천)",
    "gemini-3.8-flash": "정밀 · gemini-3.8-flash (붐빌 때 느림, 무료 키 하루 20회)",
    "gemini-3.6-flash": "보통 · gemini-3.6-flash",
}
THINKING = "high"  # 계산·사실 실수를 줄이려면 많이 생각하게 한다 (lite 기준 시간 차이는 수십 초 이내)
REQUEST_TIMEOUT = 100  # 초. 성공은 보통 10~50초. 이보다 오래 걸리면 붐비는 것으로 보고 다음 모델로 넘어간다.
RETRY_WAITS: tuple[int, ...] = ()  # 같은 모델 재시도 없음: 붐빌 때는 거절까지 오래 걸려서 다른 모델이 빠르다

# 진행 상황을 화면에 알리는 함수 (app이 넣어 준다). 예: progress.set(lambda msg: status.update(label=msg))
progress: ContextVar = ContextVar("progress", default=None)


def _notify(msg: str) -> None:
    fn = progress.get()
    if fn:
        try:
            fn(msg)
        except Exception:
            pass
MAX_SOURCE_CHARS = 60_000
MAX_ATTACH_BYTES = 18 * 1024 * 1024  # 요청 한 번에 넣을 수 있는 첨부 총량(여유 있게)


class AnalyzeError(Exception):
    """사용자에게 그대로 보여줄 수 있는 분석 실패 메시지."""


class _BrokenOutput(Exception):
    """응답이 중간에 끊긴 JSON. 다른 모델로 다시 시도한다."""


@dataclass
class Attachment:
    name: str
    mime_type: str
    data: bytes

    @property
    def kind(self) -> str:
        return "document" if self.mime_type == "application/pdf" else "image"


@dataclass
class AnalyzeResult:
    worksheet: Worksheet | None
    theme: Theme | None
    model_used: str = ""


def build_prompt(
    *,
    text: str,
    has_files: bool,
    want_content: bool,
    want_style: bool,
    level_label: str,
    make_questions: bool,
    extra: str = "",
) -> str:
    parts = ["너는 학습지 편집자다. HTML, CSS, 디자인 코드는 만들지 않는다. 모양은 프로그램이 정한다. JSON으로만 답한다."]

    if want_content:
        where = "첨부한 파일(이미지·PDF)과 <원본자료>" if has_files and text.strip() else ("첨부한 파일" if has_files else "<원본자료>")
        question_rule = (
            "원문에 학생이 답할 문항이 부족하면, 원문 내용에 근거한 문항을 3~5개 추가한다. "
            "단답형·서술형·선택형·빈칸 채우기·O/X를 골고루 쓰고, 원문에 없는 사실은 묻지 않는다."
            if make_questions
            else "원문에 없는 문항을 새로 만들지 않는다."
        )
        parts.append(f"""
## 할 일 1: 내용 구조화 → "worksheet"
{where}의 내용을 읽고, 학습지의 '의미 단위(컴포넌트)'로 나누어 순서대로 담는다.
1. 원문의 문장과 사실을 최대한 그대로 옮긴다. 임의로 요약하거나 새 내용을 지어내지 않는다.
2. 각 부분이 학습지에서 하는 역할(설명, 읽을 자료, 개념 정리, 활동, 질문 등)을 보고 가장 알맞은 컴포넌트를 고른다.
3. 학습지 사진이라면 학생이 손으로 쓴 답, 채점 표시, 낙서는 옮기지 않는다. 빈 답란은 알맞은 문항 컴포넌트로 바꾼다.
   그림·삽화·인물화·사진·도표·지도는 내용과 가까운 자리에 image 컴포넌트로 넣고, body에 무슨 그림인지 한 문장으로 적는다.
   그림으로 설명하는 학습지(만화·그림 정리)라면 그림도 내용이므로 되도록 모두 옮긴다. 테두리·배경 무늬·아주 작은 아이콘만 뺀다.
   첨부 파일 속 그림이면 file(몇 번째 첨부인지, 1부터), page(PDF 쪽 번호, 이미지는 1),
   box([ymin, xmin, ymax, xmax], 그 쪽 전체를 0~1000으로 본 좌표)로 그림 하나만 꼭 맞게 감싸는 위치를 적는다.
   그림 안에 함께 그려진 말풍선 글씨는 그림에 포함해도 되지만, 본문 글이나 문항은 box에 넣지 않는다.
4. 문항 번호, 활동 번호, 선택지 번호(1., ①, 가. 등)는 쓰지 않는다. 번호는 프로그램이 붙인다.
5. title은 반드시 채운다. 원문에 제목이 없으면 내용을 대표하는 짧은 제목을 만든다.
   과목·단원·차시·학습 목표는 원문에 있을 때만 채우고, 없으면 빈 값으로 둔다.
6. 강조가 필요한 곳에만 **굵게** 표기를 쓸 수 있다. 다른 마크다운은 쓰지 않는다.
7. 대상 학생: {level_label}.
8. {question_rule}

### 컴포넌트 목록
{component_guide()}""")

    if want_style:
        parts.append(f"""
## 할 일 {2 if want_content else 1}: 디자인 스타일 고르기 → "theme"
첨부한 학습지의 겉모습(머리말 모양, 소제목 모양, 문항 번호 모양, 글꼴 느낌, 선 굵기, 모서리, 배경, 간격)을 관찰하고,
아래 각 항목에서 가장 비슷한 값을 하나씩 고른다. 목록에 없는 값은 쓰지 않는다. 색이 있는 학습지라도 흑백으로 인쇄했을 때를 기준으로 고른다.
{theme_mod.style_guide()}""")

    if extra.strip():
        parts.append(
            f"\n## 선생님의 추가 요청\n{extra.strip()}\n"
            "(위 규칙과 충돌하면 추가 요청을 따르되, 컴포넌트·스타일 목록에 없는 형식은 만들지 않는다.)"
        )
    if text.strip():
        parts.append(f"\n<원본자료>\n{text[:MAX_SOURCE_CHARS]}\n</원본자료>")
    return "\n".join(parts)


def _schema(want_content: bool, want_style: bool) -> dict:
    props, required = {}, []
    if want_content:
        props["worksheet"] = response_schema()
        required.append("worksheet")
    if want_style:
        props["theme"] = theme_mod.response_schema()
        required.append("theme")
    return {"type": "object", "properties": props, "required": required}


def _generate(client, model: str, prompt: str, files: list[Attachment], schema: dict) -> str:
    # 최신 SDK는 Interactions API, 구버전은 generate_content를 쓴다.
    if hasattr(client, "interactions"):
        inputs: list[dict] = [{"type": "text", "text": prompt}]
        inputs += [
            {"type": f.kind, "data": base64.b64encode(f.data).decode("ascii"), "mime_type": f.mime_type}
            for f in files
        ]
        result = client.interactions.create(
            model=model,
            input=inputs if files else prompt,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema},
            generation_config={"thinking_level": THINKING},
            timeout=REQUEST_TIMEOUT,
        )
        text = result.output_text or ""
        try:
            json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
        except json.JSONDecodeError as e:
            raise _BrokenOutput(f"{model} 응답이 중간에 끊겼습니다 ({e})") from e
        return text

    from google.genai import types

    contents: list = [prompt] + [types.Part.from_bytes(data=f.data, mime_type=f.mime_type) for f in files]
    result = client.models.generate_content(
        model=model,
        contents=contents,
        config={"response_mime_type": "application/json", "response_json_schema": schema},
    )
    return result.text


def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AnalyzeError(f"AI 응답을 JSON으로 읽지 못했습니다. 다시 시도해 주세요. ({e})") from e
    if not isinstance(data, dict):
        raise AnalyzeError("AI 응답 형식이 올바르지 않습니다. 다시 시도해 주세요.")
    return data


def analyze(
    *,
    api_key: str,
    text: str = "",
    files: list[Attachment] | None = None,
    want_content: bool = True,
    want_style: bool = False,
    model: str = MODELS[0],
    level_label: str = "중학교",
    make_questions: bool = False,
    extra: str = "",
    style_name: str = "참고 학습지 스타일",
) -> AnalyzeResult:
    files = files or []
    if not (want_content or want_style):
        raise AnalyzeError("'내용'과 '스타일' 중 하나 이상을 골라 주세요.")
    if not text.strip() and not files:
        raise AnalyzeError("분석할 자료가 비어 있습니다.")
    if want_style and not files:
        raise AnalyzeError("스타일을 가져오려면 학습지 이미지나 PDF를 올려 주세요.")
    if sum(len(f.data) for f in files) > MAX_ATTACH_BYTES:
        raise AnalyzeError("첨부 파일이 너무 큽니다(합계 18MB 이하). 필요한 쪽만 올리거나 이미지 크기를 줄여 주세요.")
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")

    from google import genai  # AI 없이(직접 만들기만) 쓸 때는 불러오지 않도록 늦게 불러온다.

    prompt = build_prompt(
        text=text,
        has_files=bool(files),
        want_content=want_content,
        want_style=want_style,
        level_label=level_label,
        make_questions=make_questions,
        extra=extra,
    )
    client = genai.Client(api_key=api_key.strip())
    raw, model_used = _generate_with_retry(client, model, prompt, files, _schema(want_content, want_style))

    data = _parse_json(raw)
    if want_content and files and isinstance(data.get("worksheet"), dict):
        fill_images(data["worksheet"], files)
    try:
        ws = Worksheet.model_validate(data.get("worksheet") or {}) if want_content else None
        th = Theme.model_validate({**(data.get("theme") or {}), "name": style_name}) if want_style else None
    except ValidationError as e:
        raise AnalyzeError(f"AI 응답이 학습지 구조와 맞지 않습니다. 다시 시도해 주세요.\n{e}") from e
    return AnalyzeResult(worksheet=ws, theme=th, model_used=model_used)


def fill_images(worksheet: dict, files: list[Attachment]) -> int:
    """AI가 알려준 위치(file·page·box)대로 원본에서 그림을 잘라 image 구성요소에 넣는다. 넣은 개수를 돌려준다."""
    from .images import PageSource

    pages = PageSource([(f.mime_type, f.data) for f in files])
    filled = 0
    for b in worksheet.get("blocks") or []:
        if not isinstance(b, dict) or b.get("type") != "image":
            continue
        box = b.pop("box", None)
        file_no, page_no = b.pop("file", None) or 1, b.pop("page", None) or 1
        if box:
            try:
                b["image"] = pages.crop(int(file_no), int(page_no), box)
            except (TypeError, ValueError):
                b["image"] = ""
            filled += bool(b["image"])
    return filled


# ----- 오류 처리 -----
def _status(e: Exception) -> int | None:
    for attr in ("status_code", "code"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    m = re.search(r"Error code: (\d{3})", str(e))
    return int(m.group(1)) if m else None


def _kind(e: Exception) -> str:
    """busy(잠시 후 되는 오류) / quota(한도 초과) / key(키 문제) / model(모델 없음) / other"""
    status, text = _status(e), str(e)
    if status in (401, 403) or "API key" in text or "API_KEY" in text:
        return "key"
    if status == 429 or "RESOURCE_EXHAUSTED" in text:
        return "quota"
    if status == 404:
        return "model"
    if isinstance(e, _BrokenOutput) or (status and status >= 500) or "UNAVAILABLE" in text or "high demand" in text or type(e).__name__ in (
        "APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout",
    ):
        return "busy"
    return "other"


_MESSAGES = {
    "busy": "Gemini 서버가 지금 붐비거나 연결이 불안정합니다. 여러 모델로 다시 시도했지만 실패했어요. 1~2분 뒤에 다시 눌러 주세요.",
    "quota": "Gemini 사용 한도를 넘었습니다. 1분쯤 뒤에 다시 시도해 주세요. 무료 키라면 하루 한도가 끝났을 수 있어요(내일 다시 가능).",
    "key": "API 키가 올바르지 않거나 권한이 없습니다. .env 파일의 GEMINI_API_KEY를 확인해 주세요.",
    "model": "선택한 AI 모델을 찾을 수 없습니다. 왼쪽 설정에서 다른 모델을 골라 주세요.",
}


def _generate_with_retry(client, model: str, prompt: str, files: list[Attachment], schema: dict) -> tuple[str, str]:
    order = [model] + [m for m in MODELS if m != model]
    return _with_retry(order, lambda m: _generate(client, m, prompt, files, schema))


def _with_retry(order: list[str], call):
    """서버가 붐비면 같은 모델로 잠깐 기다렸다 다시 시도하고, 그래도 안 되면 다음 모델로 넘어간다.
    call(model)의 결과와 실제로 쓴 모델을 돌려준다."""
    last: Exception | None = None
    start = time.perf_counter()
    for n, m in enumerate(order):
        for wait in (0, *RETRY_WAITS):
            if wait:
                time.sleep(wait)
            elapsed = int(time.perf_counter() - start)
            _notify(f"{MODEL_LABELS.get(m, m).split(' (')[0]} 모델로 만드는 중…"
                    + (f" (앞 모델이 붐벼서 바꿨어요 · {elapsed}초 지남)" if n else ""))
            try:
                return call(m), m
            except AnalyzeError:
                raise
            except Exception as e:  # SDK 예외 종류가 버전마다 달라 여기서 분류한다.
                last, kind = e, _kind(e)
                if kind == "busy":
                    continue  # 같은 모델로 다시
                if kind in ("quota", "model"):
                    break  # 한도·모델 문제는 모델별이라 다음 모델로
                raise AnalyzeError(_MESSAGES.get(kind) or f"Gemini 호출에 실패했습니다: {e}") from e
    kind = _kind(last) if last else "busy"
    raise AnalyzeError(f"{_MESSAGES.get(kind, _MESSAGES['busy'])}\n(자세한 내용: {last})") from last


# ----- 성취기준으로 새 학습지 만들기 (학부모 모드) -----
DIFFICULTY = {
    "basic": ("기초", "교과서의 가장 기본 개념을 확인하는 쉬운 문항 위주. 문장은 짧게, 계산은 간단한 수로."),
    "standard": ("기본", "교과서 본문·익힘책 수준. 개념 확인과 간단한 적용 문항을 고르게."),
    "advanced": ("심화", "기본 문항에 더해 생활 속 적용, 이유를 쓰는 서술형, 두 단계 이상 생각하는 문항을 늘린다."),
}


_QUESTION_END = re.compile(r"(보세요|구하세요|쓰세요|고르세요|하시오|구하시오|인가요|일까요|할까요|까요|\?)\s*[.?]?\s*$")


def _split_diagram_questions(ws: Worksheet) -> Worksheet:
    """AI가 도식 설명 칸에 문제를 넣었으면, 도식 바로 뒤에 번호·답 칸이 있는 문항으로 떼어 낸다."""
    from .schema import Block

    from .schema import QUESTION_TYPES

    out = []
    for k, b in enumerate(ws.blocks):
        out.append(b)
        if b.type not in ("diagram", "image"):
            continue
        body = b.body.strip()
        nxt = ws.blocks[k + 1] if k + 1 < len(ws.blocks) else None
        # 정답이 없는 '~살펴보세요' 같은 안내이고 바로 뒤에 진짜 문항이 있으면 떼어 내지 않는다 (정답 없는 문항이 생기므로)
        if not b.answer.strip() and (not _QUESTION_END.search(body) or (nxt is not None and nxt.type in QUESTION_TYPES)):
            continue
        out.append(Block(type="short_answer", body=body, answer=b.answer, solution=b.solution, lines=1))
        b.body, b.answer, b.solution = "", "", ""
    ws.blocks = out
    return ws


def _with_kind(ws: Worksheet, kind: str) -> Worksheet:
    """사용자가 고른 학습지 유형을 붙이고, 그 유형에 꼭 있어야 할 칸을 채운다."""
    from .schema import Block

    ws.kind = kind if kind in kinds_mod.KINDS else "standard"
    if ws.kind == "game" and not any(b.type == "bingo" for b in ws.blocks):
        ws.blocks.append(Block(type="bingo", title="정답 빙고"))  # 빙고판은 문항 정답으로 프로그램이 만든다
    if ws.kind == "test":
        ws.goals = []
    return ws


def _postprocess(ws: Worksheet, grade: int = 0) -> Worksheet:
    """grade: 초등 학년 (모르면 0). 분수 답을 약분할지 정하는 데 쓴다 — 약분은 5학년에서 배운다.
    계산 문제 정답을 프로그램이 다시 계산해 바로잡고, 고친 내용은 ws에 잠시 붙여 둔다(LAST_FIXES)."""
    from . import verify

    ws = _fix_choice_answers(_split_diagram_questions(ws))
    verify.strip_numbers(ws)
    verify.drop_empty_tables(ws)
    LAST_FIXES.set(verify.check(ws, reduce=grade >= 5))
    return ws


LAST_FIXES: ContextVar[list] = ContextVar("last_fixes", default=[])  # 마지막으로 프로그램이 고친 정답 (화면 안내용)


def fraction_rule(grade: int) -> str:
    """분수 답 쓰는 법: 약분·통분은 5학년에서 배운다 (2022 개정 6수01)."""
    if grade >= 5:
        return "- 분수 계산의 답은 기약분수로 쓴다(예: 6/12가 아니라 1/2)."
    if grade >= 3:
        return ("- 약분은 5학년에서 배우므로 분수 답을 약분하지 않는다(예: 1/6 + 3/6 = 4/6, 2/4를 1/2로 바꾸지 않는다). "
                "분수의 덧셈·뺄셈은 분모가 같은 분수끼리만 낸다.")
    return ""


def _fix_choice_answers(ws: Worksheet) -> Worksheet:
    """선택형 정답을 '② 실제 선택지 내용'으로 맞춘다 (AI는 번호만 정확히 주면 된다)."""
    from .render import CIRCLED, _option_text

    for b in ws.blocks:
        if b.type != "multiple_choice" or not b.items:
            continue
        m = re.match(r"\s*(?:([①-⑩])|\(?(\d{1,2})[).번]?)", b.answer or "")
        if not m:
            continue
        idx = CIRCLED.index(m.group(1)) if m.group(1) else int(m.group(2)) - 1
        if 0 <= idx < len(b.items):
            b.answer = f"{CIRCLED[idx]} {_option_text(b.items[idx])}"
    return ws


MIX_RULES = {
    "separate": "- 고른 성취기준이 여러 개이므로, 영역(또는 성취기준)마다 section(소제목)을 나누고 차례대로 다룬다. 문항 수는 고르게 나눈다.",
    "combined": "- 고른 성취기준 여러 개를 한 상황 안에서 함께 써야 풀 수 있는 복합 문제 위주로 만든다"
                "(예: 길이를 재고 그 값으로 덧셈하기, 자료를 표로 정리하고 그 수로 계산하기). 개념 정리는 성취기준별로 짧게 둔다.",
}
MIX_LABELS = {"separate": "영역별로 차례대로", "combined": "여러 내용을 엮은 복합 문제"}


def generate_for_standards(
    *,
    api_key: str,
    grade: int,
    subject: str,
    standards: list,  # curriculum.Standard 목록
    unit_name: str = "",
    difficulty: str = "standard",
    n_questions: int = 8,
    extra: str = "",
    model: str = MODELS[0],
    mix: str = "separate",  # 고른 내용이 여러 개일 때: separate(영역별로 차례대로) / combined(엮은 복합 문제)
    kind: str = "standard",  # 학습지 유형 (kinds.KINDS)
) -> AnalyzeResult:
    """2022 개정 교육과정 성취기준에 맞는 초등 학습지를 새로 만든다. 모든 문항에 정답·해설을 붙인다."""
    if not standards:
        raise AnalyzeError("배울 내용(성취기준)을 하나 이상 골라 주세요.")
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")

    diff_name, diff_rule = DIFFICULTY.get(difficulty, DIFFICULTY["standard"])
    std_lines = "\n".join(
        f"- [{s.code}] ({s.area}{' > ' + s.topic if s.topic else ''}) {s.text}"
        + (f"\n  해설: {s.explain}" if s.explain else "")
        for s in standards
    )
    lower = grade <= 2
    prompt = f"""너는 초등학교 {grade}학년 {subject} 학습지를 만드는 경험 많은 교사다.
아래 2022 개정 교육과정 성취기준에 맞춰, 가정에서 아이가 혼자 풀 수 있는 학습지를 새로 만든다. JSON으로만 답한다.
HTML, CSS, 디자인은 만들지 않는다. 모양은 프로그램이 정한다.

## 성취기준
{std_lines}
{f"## 아이 교과서의 단원명{chr(10)}{unit_name.strip()} — 이 단원의 흐름과 용어에 맞춘다." if unit_name.strip() else ""}

## 만들 학습지
- 대상: 초등 {grade}학년. 난이도: {diff_name} — {diff_rule}
{f"- 문항 수: 꼭 {n_questions}개 (선생님이 정한 수 — 번호가 붙는 문항 구성요소의 개수). ""단답형·선택형·빈칸 채우기·O/X·서술형을 내용에 맞게 섞는다." if kind == "standard" else ""}
{MIX_RULES.get(mix, MIX_RULES["separate"]) if len(standards) > 1 else ""}
## 학습지 유형: {kinds_mod.label(kind)}
{kinds_mod.rule(kind, n_questions)}

- 학습 목표(goals)는 성취기준을 아이 눈높이 말로 1~2개 쓴다. subject는 "{subject}", unit은 단원명(없으면 영역 이름), title은 아이가 흥미를 가질 짧은 제목.
- 모든 문항에 answer(정답)와 solution(해설)을 쓴다. 해설은 부모가 아이에게 설명해 줄 수 있게 1~2문장.
  선택형 answer는 정답 선택지의 번호만 ①~⑤로 쓴다(예: ②). O/X는 '(1) O (2) X'처럼 쓴다. 빈칸 채우기는 본문에 [[정답]]으로 표시한다.
- 문항 번호·선택지 번호는 쓰지 않는다(프로그램이 붙인다).
{"- 1~2학년이므로 문장을 아주 짧고 쉽게 쓰고, 한자어를 줄이고, 지시문은 '~해 보세요' 말투로 쓴다." if lower else "- 지시문은 '~하시오' 대신 '~해 보세요' 말투로 쓴다."}
- 수·도형·측정·자료를 다루는 내용이면 이해를 돕는 diagram(도식)을 1~3개 넣는다 (묶음·배열·수직선·분수·시계·그래프·그림그래프·도형·각도·수 모형). 도식은 프로그램이 diagram_json대로 정확히 그린다.
  묶음 그림의 item은 문제 상황에 맞는 이모지(예: 도토리 🌰, 축구공 ⚽)로 한다. 과목과 상관없이 장면이나 등장인물을 떠올리게 하면 좋은 곳 1~2군데에는 scene(그림 카드: 이모지 그림 + 짧은 이름)을 넣어 학습지를 친근하게 만든다.
  도식의 숫자는 문항·정답과 반드시 맞아야 한다. 도식이 정답을 그대로 보여 주면 안 되는 문항이라면 정답 부분을 hidden(수직선)·show_value:false(각도)·show_hands:false(시계)로 가린다. 도식의 body는 '축구공 상자 4개'처럼 그림 설명만 짧게 쓰고(질문 금지), answer·solution은 비운다. 도식을 보고 푸는 질문은 도식 바로 뒤에 short_answer 등 별도 문항으로 쓴다.
- image 컴포넌트는 쓰지 않는다.
- 문항이 '위 대화·글·표·그림·수직선'을 가리키면 그 자료(reading·text·table·diagram)를 반드시 문항 앞에 넣는다. 정답이 문제 글에 드러나지 않게 한다(영어 첫 글자·철자·낱말을 묻는 문제에 그 영어 단어를 쓰지 않는다 — '강아지'처럼 한국어 뜻이나 그림으로 묻고, 그 앞 그림 카드 labels도 한국어로 쓴다).

## 학년 범위 (가장 중요 — 어기면 안 된다)
{curriculum.limits(grade, subject, standards)}

## 정확성 규칙 (가장 중요)
- 성취기준과 해설의 범위를 벗어나지 않는다. 다음 학년에서 배우는 개념을 미리 쓰지 않는다.
- 수학 계산 문항은 정답을 반드시 한 번 더 계산해 확인한다.
{fraction_rule(grade)}
- 성취기준에 적힌 수의 범위를 지킨다(예: '세 자리 수의 덧셈'이면 1000보다 작은 수만, 돈 계산도 그 범위 안에서).
- 확실하지 않은 사실, 연도, 수치, 인물 정보는 쓰지 않는다.
- 특정 출판사 교과서 문장을 그대로 베끼지 않고 새로 쓴다.
- 영어는 지시문을 한국어로 쓰고, 영어 문장은 그 학년 수준의 쉽고 짧은 문장만 쓴다.
{f"{chr(10)}## 추가 요청{chr(10)}{extra.strip()}" if extra.strip() else ""}

## 컴포넌트 목록
{component_guide()}
"""
    from google import genai

    client = genai.Client(api_key=api_key.strip())
    raw, model_used = _generate_with_retry(client, model, prompt, [], response_schema())
    data = _parse_json(raw)
    data = data.get("worksheet", data) if isinstance(data, dict) else data
    try:
        ws = Worksheet.model_validate(data)
    except ValidationError as e:
        raise AnalyzeError(f"AI 응답이 학습지 구조와 맞지 않습니다. 다시 시도해 주세요.\n{e}") from e
    if not ws.subject:
        ws.subject = subject
    return AnalyzeResult(worksheet=_postprocess(_with_kind(ws, kind), grade), theme=None, model_used=model_used)


def generate_from_outline(
    *,
    api_key: str,
    title: str,
    subject: str = "",
    unit: str = "",
    goals: list[str] | None = None,
    level_label: str = "중학교",
    difficulty: str = "standard",
    n_questions: int = 8,
    extra: str = "",
    model: str = MODELS[0],
) -> AnalyzeResult:
    """선생님이 채운 머리말(제목·과목·단원·학습 목표)을 보고 학습지 내용을 새로 만든다. 문항마다 정답·해설을 붙인다."""
    goals = [g for g in (goals or []) if g.strip()]
    if not (title.strip() and title.strip() != "새 학습지") and not unit.strip() and not goals:
        raise AnalyzeError("머리말에 학습지 제목이나 단원, 학습 목표를 먼저 적어 주세요. AI가 그걸 보고 내용을 만들어요.")
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")

    diff_name, diff_rule = DIFFICULTY.get(difficulty, DIFFICULTY["standard"])
    goal_lines = "\n".join(f"- {g}" for g in goals) or "- (없음: 제목과 단원에 맞게 1~2개 만든다)"
    prompt = f"""너는 경험 많은 {level_label} {subject or ''} 교사다. 아래 머리말에 맞는 수업용 학습지 내용을 새로 만든다. JSON으로만 답한다.
HTML, CSS, 디자인은 만들지 않는다. 모양은 프로그램이 정한다.

## 머리말
- 제목: {title.strip() or '(없음)'}
- 과목: {subject.strip() or '(없음)'}
- 단원: {unit.strip() or '(없음)'}
- 학습 목표:
{goal_lines}

## 만들 학습지
- 대상: {level_label}. 난이도: {diff_name} — {diff_rule}
- 문항 수: 꼭 {n_questions}개 (선생님이 정한 수 — 번호가 붙는 문항 구성요소의 개수). 단답형·선택형·빈칸 채우기·O/X·서술형을 학습 목표에 맞게 섞는다.
- 순서: section(소제목)으로 도입·전개·정리를 나누고, concept(핵심 개념)·reading(읽기 자료)·activity(활동)를 필요한 만큼 넣은 뒤 문항, 마지막에 reflection(스스로 점검).
- 학습 목표를 빠짐없이 다루고, 목표에 없는 내용으로 넓히지 않는다.
- 모든 문항에 answer(정답)와 solution(해설 1~2문장)을 쓴다. 선택형은 정답 번호만 ①~⑤로(예: ②), O/X는 '(1) O (2) X'처럼. 빈칸은 본문에 [[정답]]으로.
- 문항 번호·선택지 번호는 쓰지 않는다(프로그램이 붙인다). image 컴포넌트는 쓰지 않는다.
- 수·도형·측정·자료를 다루는 내용이면 이해를 돕는 diagram(도식)을 1~3개 넣는다 (묶음·배열·수직선·분수·시계·그래프·그림그래프·도형·각도·수 모형). 도식은 프로그램이 diagram_json대로 정확히 그린다.
  묶음 그림의 item은 문제 상황에 맞는 이모지(예: 도토리 🌰, 축구공 ⚽)로 한다. 과목과 상관없이 장면이나 등장인물을 떠올리게 하면 좋은 곳 1~2군데에는 scene(그림 카드: 이모지 그림 + 짧은 이름)을 넣어 학습지를 친근하게 만든다.
  도식의 숫자는 문항·정답과 반드시 맞아야 한다. 도식이 정답을 그대로 보여 주면 안 되는 문항이라면 정답 부분을 hidden(수직선)·show_value:false(각도)·show_hands:false(시계)로 가린다. 도식의 body는 '축구공 상자 4개'처럼 그림 설명만 짧게 쓰고(질문 금지), answer·solution은 비운다. 도식을 보고 푸는 질문은 도식 바로 뒤에 short_answer 등 별도 문항으로 쓴다.

## 정확성 규칙
- 확실하지 않은 사실, 연도, 수치, 인물 정보는 쓰지 않는다. 계산 문항은 정답을 한 번 더 확인한다.
- 특정 교과서 문장을 그대로 베끼지 않고 새로 쓴다.
{f"{chr(10)}## 추가 요청{chr(10)}{extra.strip()}" if extra.strip() else ""}

## 컴포넌트 목록
{component_guide()}
"""
    from google import genai

    client = genai.Client(api_key=api_key.strip())
    raw, model_used = _generate_with_retry(client, model, prompt, [], response_schema())
    data = _parse_json(raw)
    data = data.get("worksheet", data) if isinstance(data, dict) else data
    try:
        ws = Worksheet.model_validate(data)
    except ValidationError as e:
        raise AnalyzeError(f"AI 응답이 학습지 구조와 맞지 않습니다. 다시 시도해 주세요.\n{e}") from e
    return AnalyzeResult(worksheet=_postprocess(ws), theme=None, model_used=model_used)


# ----- 과목융합형: 같은 학년의 두세 과목을 엮은 과제 -----
TASK_TYPES = {
    "project": ("프로젝트 과제", "실생활 주제 하나로 여러 과목을 함께 쓰는 조사·만들기·발표 활동 2~3개를 중심으로 하고, 활동 사이사이에 확인 문항을 넣는다."),
    "story": ("이야기 속 문제 풀이", "하나의 이야기나 상황(예: 가족 여행, 학급 장터) 속에서 여러 과목의 문항을 차례로 푼다."),
    "inquiry": ("탐구 활동지", "궁금한 질문 → 자료 찾기·관찰 → 표나 그래프로 정리 → 결론 쓰기 순서로 탐구한다."),
    "separate": ("과목별로 나눠서", "억지로 엮지 않고, 과목마다 section(소제목: 과목 이름과 배울 내용)으로 나눠 그 과목 문항을 차례로 푼다."),
}


def generate_integrated(
    *,
    api_key: str,
    grade: int,
    picks: dict,  # 과목 → 고른 성취기준 목록 (비어 있으면 후보 목록에서 AI가 고른다)
    candidates: dict,  # 과목 → 그 학년의 성취기준 전체
    task_type: str = "project",
    theme: str = "",
    difficulty: str = "standard",
    n_questions: int = 8,
    extra: str = "",
    model: str = MODELS[0],
    kind: str = "standard",  # 학습지 유형 (kinds.KINDS)
) -> AnalyzeResult:
    """같은 학년의 두세 과목 성취기준을 엮어 하나의 융합 과제 학습지를 만든다."""
    subjects = list(picks)
    if len(subjects) < 2:
        raise AnalyzeError("엮을 과목을 두 개 이상 골라 주세요.")
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")

    def lines(stds) -> str:
        return "\n".join(f"  - [{s.code}] ({s.area}) {s.text}" for s in stds)

    blocks = []
    for subj in subjects:
        if picks[subj]:
            blocks.append(f"### {subj} — 반드시 다룰 성취기준\n{lines(picks[subj])}")
        else:
            blocks.append(f"### {subj} — 아래 중 주제와 가장 잘 어울리는 1~2개를 골라 다룬다\n{lines(candidates[subj])}")
    task_name, task_rule = TASK_TYPES.get(task_type, TASK_TYPES["project"])
    diff_name, diff_rule = DIFFICULTY.get(difficulty, DIFFICULTY["standard"])
    joined = " · ".join(subjects)

    separate = task_type == "separate"
    prompt = f"""너는 초등학교 {grade}학년 담임 교사이자 융합 수업 전문가다.
아래 과목들의 2022 개정 교육과정 성취기준을 {"과목별로 나눠 한 장에 담은" if separate else "자연스럽게 엮은 '과목융합형' 과제"} 학습지를 새로 만든다. JSON으로만 답한다.
HTML, CSS, 디자인은 만들지 않는다. 모양은 프로그램이 정한다.

## 엮을 과목: {joined}
{chr(10).join(blocks)}

## 과제 형태: {task_name}
{task_rule}
{f"- 주제/상황: {theme.strip()} — 이 주제로 과목들을 엮는다." if theme.strip() and not separate
  else f"- 주제/상황: {theme.strip()} — 문항의 소재로 쓴다." if theme.strip()
  else "- 과목마다 그 과목답게 만든다." if separate
  else "- 주제/상황: 두 과목이 억지스럽지 않게 만나는 생활 속 주제를 하나 정한다."}

## 만들 학습지
- 대상: 초등 {grade}학년. 난이도: {diff_name} — {diff_rule}
- 어느 한 과목이 들러리가 되지 않게, 각 과목의 성취기준이 실제로 쓰이도록 한다.
  (예: 수학+사회라면 우리 고장 조사 자료를 표·그래프로 정리하고 해석하기)
- 확인 문항 꼭 {n_questions}개.{" 소제목이 과목을 알려 주므로 문항 앞에 과목 표시는 하지 않는다." if separate else " 각 문항 body 맨 앞에 [수학], [사회]처럼 그 문항이 쓰는 과목을 표시한다."}
{("- 순서: " + ("과목마다 section → (필요하면 text·concept) → 문항, 과목 순서는 위 목록 순서 → 마지막 reflection(과목별 점검)." if separate
  else "section(주제 소개) → text 또는 reading(상황·자료) → activity와 문항 → 마지막 reflection(과목별 스스로 점검 포함).")) if kind == "standard"
  else f"## 학습지 유형: {kinds_mod.label(kind)}{chr(10)}{kinds_mod.rule(kind, n_questions)}{chr(10)}- 과목이 여럿이므로 위 유형의 짜임새 안에서 과목마다 고르게 다룬다."}
- subject는 "{joined}{"" if separate else " 융합"}", unit은 {"다루는 내용을 짧게" if separate else "주제"}, title은 아이가 흥미를 가질 짧은 제목. goals는 과목별로 1개씩.
- 모든 문항에 answer와 solution을 쓴다. 선택형은 정답 번호만 ①~⑤로(예: ②), O/X는 '(1) O (2) X'처럼. 빈칸은 [[정답]].
- 문항 번호·선택지 번호는 쓰지 않는다(프로그램이 붙인다).
- 수·도형·측정·자료를 다루는 내용이면 이해를 돕는 diagram(도식)을 1~3개 넣는다 (묶음·배열·수직선·분수·시계·그래프·그림그래프·도형·각도·수 모형). 도식은 프로그램이 diagram_json대로 정확히 그린다.
  묶음 그림의 item은 문제 상황에 맞는 이모지(예: 도토리 🌰, 축구공 ⚽)로 한다. 과목과 상관없이 장면이나 등장인물을 떠올리게 하면 좋은 곳 1~2군데에는 scene(그림 카드: 이모지 그림 + 짧은 이름)을 넣어 학습지를 친근하게 만든다.
  도식의 숫자는 문항·정답과 반드시 맞아야 한다. 도식이 정답을 그대로 보여 주면 안 되는 문항이라면 정답 부분을 hidden(수직선)·show_value:false(각도)·show_hands:false(시계)로 가린다. 도식의 body는 '축구공 상자 4개'처럼 그림 설명만 짧게 쓰고(질문 금지), answer·solution은 비운다. 도식을 보고 푸는 질문은 도식 바로 뒤에 short_answer 등 별도 문항으로 쓴다.
- image 컴포넌트는 쓰지 않는다.
- 문항이 '위 대화·글·표·그림·수직선'을 가리키면 그 자료(reading·text·table·diagram)를 반드시 문항 앞에 넣는다. 정답이 문제 글에 드러나지 않게 한다(영어 첫 글자·철자·낱말을 묻는 문제에 그 영어 단어를 쓰지 않는다 — '강아지'처럼 한국어 뜻이나 그림으로 묻고, 그 앞 그림 카드 labels도 한국어로 쓴다).
- 지시문은 '~해 보세요' 말투로 쓴다.

## 학년 범위 (가장 중요 — 어기면 안 된다)
{chr(10).join(curriculum.limits(grade, subj, picks[subj]) for subj in subjects)}

## 정확성 규칙
- 각 과목 성취기준의 범위를 벗어나지 않는다. 다음 학년에서 배우는 개념을 미리 쓰지 않는다.
- 계산 문항은 정답을 반드시 다시 계산해 확인한다. 확실하지 않은 사실·연도·수치는 쓰지 않는다.
{fraction_rule(grade)}
- 수학 문항은 그 학년이 배우는 수의 범위를 지킨다(예: 3학년 덧셈·뺄셈은 세 자리 수까지, 돈 계산도 그 범위 안에서).
  다룰 수학 성취기준을 직접 고르지 않았다면 위 후보 중 실제로 쓴 성취기준의 범위를 따른다.
{f"{chr(10)}## 추가 요청{chr(10)}{extra.strip()}" if extra.strip() else ""}

## 컴포넌트 목록
{component_guide()}
"""
    from google import genai

    client = genai.Client(api_key=api_key.strip())
    raw, model_used = _generate_with_retry(client, model, prompt, [], response_schema())
    data = _parse_json(raw)
    data = data.get("worksheet", data) if isinstance(data, dict) else data
    try:
        ws = Worksheet.model_validate(data)
    except ValidationError as e:
        raise AnalyzeError(f"AI 응답이 학습지 구조와 맞지 않습니다. 다시 시도해 주세요.\n{e}") from e
    if not ws.subject:
        ws.subject = f"{joined} 융합"
    return AnalyzeResult(worksheet=_postprocess(_with_kind(ws, kind), grade), theme=None, model_used=model_used)


# ----- 말로 고르기: "3학년 수학 덧셈이랑 국어 발표" → 학년·과목·성취기준 -----
_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "integer", "description": "학년 1~6"},
        "subjects": {"type": "array", "items": {"type": "string"}, "description": "과목 (국어·수학·영어·사회·과학)"},
        "codes": {"type": "array", "items": {"type": "string"}, "description": "고른 성취기준 코드"},
        "task_type": {"type": "string", "enum": list(TASK_TYPES), "description": "과목이 둘 이상일 때 과제 형태"},
        "kind": {"type": "string", "enum": list(kinds_mod.KINDS), "description": "학습지 유형"},
        "extra": {"type": "string", "description": "학년·과목·내용 말고 남은 바람 (예: 공룡이 나오게, 쉽게). 없으면 빈 문자열"},
    },
    "required": ["grade", "subjects", "codes", "task_type", "kind", "extra"],
}


def understand_request(*, api_key: str, text: str, catalog: str, default_grade: int, model: str = MODELS[0]) -> dict:
    """교사·학부모가 말로 쓴 요청에서 학년·과목·성취기준 코드를 고른다. 코드는 catalog 안에서만 고른다(검사는 앱이 한다)."""
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")
    if not text.strip():
        raise AnalyzeError("어떤 학습지가 필요한지 적어 주세요.")
    from google import genai

    prompt = f"""초등학교 학습지를 만들려는 사람의 요청이다. 요청에 맞는 학년·과목·성취기준을 아래 목록에서 고른다. JSON으로만 답한다.

## 요청
{text.strip()}

## 고르는 규칙
- grade: 요청에 나온 학년. 학년을 말하지 않았으면 내용으로 짐작한다(예: '분수의 곱셈' → 5학년, '받아올림 있는 세 자리 수 덧셈' → 3학년).
  내용으로도 짐작할 수 없으면(예: '발표', '독서') {default_grade}학년.
- subjects: 요청에 나온 과목만, 요청에 나온 순서대로 (국어·수학·영어·사회·과학). 과목 이름이 없어도 내용으로 분명하면 넣는다(예: '분수' → 수학).
- codes: 아래 목록에서 요청한 내용에 꼭 맞는 성취기준만, 과목마다 1~3개. '학년' 칸에 그 학년이 있는 것을 고른다.
  목록에 없는 코드를 만들지 않는다. 맞는 것이 없으면 그 과목은 비워 둔다.
- task_type: 과목이 둘 이상일 때만 의미가 있다. '엮어서·함께·융합·프로젝트·이야기'처럼 하나로 엮어 달라는 말이 있으면
  project(조사·만들기)·story(이야기 속 문제)·inquiry(탐구) 중 맞는 것, 그런 말이 없으면 separate(과목별로 나눠서).
- kind: 학습지 유형. 시험·평가·테스트 → test, 연산·계산·낱말 연습·반복·드릴 → drill, 정리·노트·요약 → note,
  탐구·실험·관찰·조사 활동 → inquiry, 놀이·게임·빙고 → game, 말이 없으면 standard.
- extra: 학년·과목·배울 내용·유형 말고 남은 바람만 짧게 (예: '공룡이 나오게', '쉽게', '문제 10개'). 없으면 빈 문자열.

## 성취기준 목록 (코드 | 과목 | 학년 | 영역 | 내용)
{catalog}
"""
    client = genai.Client(api_key=api_key.strip())

    def call(m: str) -> dict:
        result = client.interactions.create(
            model=m, input=prompt,
            response_format={"type": "text", "mime_type": "application/json", "schema": _REQUEST_SCHEMA},
            generation_config={"thinking_level": "low"}, timeout=REQUEST_TIMEOUT,
        )
        try:
            return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", (result.output_text or "").strip()))
        except json.JSONDecodeError as e:
            raise _BrokenOutput(str(e)) from e

    data = _with_retry([model] + [m for m in MODELS if m != model], call)[0]
    return data if isinstance(data, dict) else {}


# ----- 내 학습지 양식: 문제를 넣을 칸 찾기 -----
_FORM_SCHEMA = {
    "type": "object",
    "properties": {
        "regions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer", "description": "몇 번째 쪽인지 (1부터)"},
                    "kind": {"type": "string", "enum": ["content", "title"]},
                    "box": {"type": "array", "items": {"type": "integer"},
                            "description": "[ymin, xmin, ymax, xmax], 그 쪽 전체를 0~1000으로 본 좌표"},
                    "erase": {"type": "boolean", "description": "칸 안에 이미 인쇄된 문제·글이 있어 지워야 하면 true"},
                },
                "required": ["page", "kind", "box", "erase"],
            },
        },
    },
    "required": ["regions"],
}


def find_form_regions(*, api_key: str, pages: list[bytes], model: str = MODELS[0]) -> list[list[dict]]:
    """학습지 양식 쪽 그림(JPEG)들에서 제목 칸·문제 칸 위치를 찾는다. 쪽마다 칸 목록을 돌려준다."""
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")
    from google import genai

    prompt = f"""첨부한 그림 {len(pages)}장은 학습지 양식의 1~{len(pages)}쪽이다. 이 양식의 모양(머리말·테두리·장식·로고)은 그대로 두고,
정해진 칸 안에 새 문제를 넣으려 한다. 쪽마다 아래 칸의 위치를 찾아라.

- content (문제 칸): 문제·활동을 적어 넣을 넓은 영역.
  · 머리말(제목, 학년·반·번호·이름 칸), 쪽 번호, 꼬리말, 로고, 장식은 빼고 그 안쪽 본문 영역만.
  · 테두리 선이 있으면 선과 겹치지 않게 선 안쪽으로 조금 들여서 잡는다.
  · 본문이 2단(왼쪽·오른쪽)이나 여러 상자로 나뉘어 있으면 상자마다 따로, 읽는 순서대로(위→아래, 2단이면 왼쪽 단 먼저).
  · 문제마다 작은 상자가 줄지어 있는 양식이면 작은 상자를 하나하나 잡지 말고, 그 상자들이 있는 본문 영역 전체를 한 칸으로 잡는다.
  · 쪽마다 적어도 1개.
- title (제목 칸): 학습지 제목을 적는 자리가 있으면 쪽마다 1개까지. 학년·반·번호·이름을 적는 칸은 제목 칸이 아니다(그대로 둔다).
- erase: 칸 안에 이미 인쇄된 문제·글·제목이 있으면 true(흰색으로 덮고 새 내용을 넣는다).
  비어 있거나, 칸 안의 줄·격자만 있으면 false.
- box: [ymin, xmin, ymax, xmax], 그 쪽 전체를 0~1000으로 본 좌표. page는 1부터.
"""
    files = [Attachment(f"page{i + 1}.jpg", "image/jpeg", data) for i, data in enumerate(pages)]
    client = genai.Client(api_key=api_key.strip())
    raw, _ = _generate_with_retry(client, model, prompt, files, _FORM_SCHEMA)
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    except json.JSONDecodeError as e:
        raise AnalyzeError("양식을 분석하지 못했어요. 다시 시도해 주세요.") from e
    out: list[list[dict]] = [[] for _ in pages]
    for r in data.get("regions") or []:
        if not isinstance(r, dict):
            continue
        n = r.get("page")
        if isinstance(n, int) and 1 <= n <= len(pages) and isinstance(r.get("box"), list) and len(r["box"]) == 4:
            out[n - 1].append({"kind": r.get("kind") if r.get("kind") in ("content", "title") else "content",
                               "box": r["box"], "erase": bool(r.get("erase"))})
    return out


# ----- 내 학습지 양식(PDF·사진): 한글 양식처럼 머리·문제 자리·꼬리로 나누기 -----
_BOX = {"type": "array", "items": {"type": "integer"}, "description": "[ymin, xmin, ymax, xmax], 쪽 전체를 0~1000으로 본 좌표"}
_LAYOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "head_end": {"type": "integer", "description": "머리(제목·단원·학년·반·이름 칸·학습 목표 칸)의 아래 끝 y (0~1000)"},
        "body": {**_BOX, "description": "문제·활동·답 칸이 있는 본문 영역 [ymin, xmin, ymax, xmax]. 머리·꼬리는 뺀다"},
        "foot_end": {"type": "integer", "description": "본문 아래 꼬리(※ 안내, 출처 등)의 아래 끝 y. 꼬리가 없으면 body의 ymax"},
        "texts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "box": _BOX,
                    "text": {"type": "string", "description": "그 자리에 적힌 글 그대로"},
                    "role": {"type": "string", "enum": ["title", "unit", "goals", "other"]},
                },
                "required": ["box", "text", "role"],
            },
            "description": "머리와 꼬리에 적힌 글 줄",
        },
        "layout": {"type": "string", "enum": ["lines", "table", "boxes"]},
        "number": {"type": "string", "enum": ["dot", "paren", "circle", "box"]},
        "section": {"type": "string", "enum": ["plain", "bar", "underline", "box"]},
        "question_bold": {"type": "boolean"},
        "serif": {"type": "boolean", "description": "본문 글꼴이 명조·바탕 계열이면 true"},
        "label_box": {**_BOX, "description": "본문에서 번호·이름표가 적힌 칸 하나의 위치. 그런 칸이 없으면 빈 배열"},
    },
    "required": ["head_end", "body", "foot_end", "texts", "layout", "number", "section", "question_bold", "serif",
                 "label_box"],
}


def analyze_form_page(*, api_key: str, page: bytes, model: str = MODELS[0]) -> dict:
    """학습지 양식 첫 쪽 그림(JPEG)에서 머리·문제 자리·꼬리의 위치, 머리·꼬리의 글, 문제 자리의 짜임(정해진 선택지)을 찾는다.
    색·선 굵기·간격 같은 모양 값은 묻지 않는다 — docform이 그림에서 직접 잰다."""
    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")
    from google import genai

    prompt = """첨부한 그림은 학습지 양식의 첫 쪽이다. 이 양식의 머리와 꼬리는 그대로 두고, 본문(문제 자리)의 원래 문제는 지운 뒤
새 문제를 이 양식과 같은 짜임으로 채우려 한다. 아래를 찾아라. 좌표는 모두 쪽 전체를 0~1000으로 본 값이다.

- head_end: 머리의 아래 끝. 머리 = 쪽 맨 위의 제목, 단원·차시, 학년·반·번호·이름 칸, '공부할 내용·학습 목표' 칸.
  첫 문제·첫 활동·첫 소제목(예: '1. 영양소 의미')은 머리가 아니다.
- body: 본문 영역 [ymin, xmin, ymax, xmax]. 문제·활동·답 칸·표가 있는 곳 전체. ymin은 head_end 근처.
  xmin·xmax는 본문 표·선의 왼쪽·오른쪽 끝.
- foot_end: 본문 아래에 '※ 안내', 출처, 학교 이름 같은 꼬리가 있으면 그 아래 끝. 없으면 body의 ymax와 같게.
  쪽 번호만 있으면 꼬리로 보지 않는다.
- texts: 머리와 꼬리에 적힌 글을 줄(또는 칸)마다. box는 글자에 꼭 맞게.
  role: 학습지 제목 = title, 단원 이름 = unit, '공부할 내용·학습 목표' 이름표 옆 칸의 목표 문장들 = goals(여러 줄이면 그 칸 전체를 한 상자로),
  나머지('이름', '학년 반', 이름표 글자, 안내) = other. 본문 안의 글은 넣지 않는다.
- layout: 본문 문제의 짜임.
  lines = 번호와 문제 글 아래에 답 쓰는 가로줄·빈 곳이 있다.
  table = 표 안에서 왼쪽 칸에 번호·이름표, 오른쪽 칸에 내용·답 칸.
  boxes = 둥근 상자·떨어진 상자에 번호·이름표 상자와 답 상자가 나란히.
- number: 문제 번호 모양. 1. = dot, 1) = paren, ① = circle, 네모 안 번호 = box.
- section: 소제목 모양. 굵은 글씨만 = plain, 왼쪽 세로 막대 = bar, 밑줄 = underline, 색 칸·상자 안 = box.
- question_bold: 문제 글이 굵은 글씨인가.
- serif: 본문 글꼴이 명조·바탕처럼 삐침이 있는 글꼴인가.
- label_box: 본문에서 번호·이름표(예: '탄수화물', '하는 일')가 적힌 칸 하나. 그런 칸이 없으면 [].
"""
    client = genai.Client(api_key=api_key.strip())
    raw, _ = _generate_with_retry(client, model, prompt, [Attachment("page1.jpg", "image/jpeg", page)], _LAYOUT_SCHEMA)
    try:
        data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip()))
    except json.JSONDecodeError as e:
        raise AnalyzeError("양식을 분석하지 못했어요. 다시 시도해 주세요.") from e
    return data if isinstance(data, dict) else {}


# ----- 고치기: 구성요소 하나를 AI가 다시 써 준다 -----
REVISE_ACTIONS: dict[str, tuple[str, str]] = {
    "easier": ("🙂 더 쉽게", "같은 내용을 더 쉬운 낱말·짧은 문장·작은 수로 바꾼다."),
    "harder": ("💪 더 어렵게", "같은 성취기준 안에서 한 단계 더 생각하게 만든다(두 단계 풀이, 이유 쓰기 등)."),
    "another": ("🔄 다른 문제로", "같은 종류·같은 개념으로 상황과 숫자를 바꾼 새 문제를 만든다."),
    "polish": ("✍️ 문장 다듬기", "뜻은 그대로 두고 맞춤법·띄어쓰기·어색한 표현을 고쳐 읽기 쉽게 다듬는다."),
    "answer": ("✅ 정답·해설 채우기", "내용은 그대로 두고 정답(answer)과 해설(solution)을 정확히 채운다. 계산은 반드시 다시 확인한다."),
}


def revise_block(
    *,
    api_key: str,
    block: dict,
    instruction: str,
    context: dict,  # 학습지 제목·과목·단원·학습 목표·대상
    model: str = MODELS[0],
) -> Block:
    """구성요소 하나를 지시대로 고친 '추천안'을 돌려준다 (적용은 화면에서 사용자가 고른다)."""
    from .schema import Block, block_schema

    if not api_key.strip():
        raise AnalyzeError("Gemini API 키가 없습니다. 왼쪽 설정에 입력해 주세요.")
    if not instruction.strip():
        raise AnalyzeError("어떻게 고칠지 골라 주세요.")
    shown = {k: v for k, v in block.items() if not k.startswith("_") and k not in ("image",) and v not in ("", [], {}, 0, None)}
    if "diagram" in shown:
        shown["diagram_json"] = json.dumps(shown.pop("diagram"), ensure_ascii=False)
    prompt = f"""너는 학습지 편집을 돕는 교사다. 아래 학습지의 구성요소 하나를 요청대로 고친다. JSON 하나로만 답한다.
HTML, CSS, 디자인은 만들지 않는다.

## 학습지 정보
- 제목: {context.get('title', '')} / 과목: {context.get('subject', '')} / 단원: {context.get('unit', '')}
- 대상: {context.get('level', '')}
- 학습 목표: {'; '.join(context.get('goals') or []) or '(없음)'}

## 지금 구성요소 (type: {block.get('type')} — {BLOCK_TYPES.get(block.get('type'), ('',))[0]})
{json.dumps(shown, ensure_ascii=False, indent=1)}

## 고칠 내용
{instruction.strip()}

## 규칙
- type은 바꾸지 않는다(요청에 다른 종류로 바꾸라는 말이 있을 때만 바꾼다). 문항이면 answer와 solution을 정확히 채운다.
- 학습 목표와 대상 학년 수준을 벗어나지 않는다. 계산 문항은 정답을 반드시 다시 계산해 확인한다.
- 문항 번호·선택지 번호는 쓰지 않는다. 선택형 answer는 정답 번호만 ①~⑤로 쓴다. 빈칸은 본문에 [[정답]]으로.
- 확실하지 않은 사실·연도·수치는 쓰지 않는다.

## 구성요소 형식
{component_guide()}
"""
    from google import genai

    client = genai.Client(api_key=api_key.strip())
    raw, _ = _generate_with_retry(client, model, prompt, [], block_schema())
    data = _parse_json(raw)
    if isinstance(data, dict) and "blocks" in data and data["blocks"]:
        data = data["blocks"][0]
    try:
        new = Block.model_validate({**data, "type": data.get("type") or block.get("type")})
    except ValidationError as e:
        raise AnalyzeError(f"AI 응답이 구성요소 형식과 맞지 않습니다. 다시 시도해 주세요.\n{e}") from e
    # 그림·크기·도식처럼 AI가 다루지 않는 값은 원래 것을 유지한다.
    new.image = block.get("image", "") if new.type == block.get("type") else ""
    new.size = block.get("size", "m")
    if not new.diagram and new.type == "diagram":
        new.diagram = block.get("diagram") or {}
    return _fix_choice_answers(Worksheet(blocks=[new])).blocks[0]


# ----- 검토: 다 만든 학습지를 AI가 한 번 더 풀어 보고 정답·학년 범위를 확인한다 -----
_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "num": {"type": "integer", "description": "문항 번호"},
                    "problem": {"type": "string", "enum": ["answer", "scope", "unclear", "fact"]},
                    "note": {"type": "string", "description": "무엇이 문제인지 한 문장"},
                    "answer": {"type": "string", "description": "problem이 answer일 때 바른 정답 (선택형은 번호만 ①~⑤)"},
                    "solution": {"type": "string", "description": "problem이 answer일 때 바른 해설 1~2문장"},
                },
                "required": ["num", "problem", "note", "answer", "solution"],
            },
        },
    },
    "required": ["issues"],
}


EXPLAIN_BASE = 100  # 검토 글에서 설명 블록 번호는 101부터 (문항 번호와 겹치지 않게)


def _review_text(ws: Worksheet) -> tuple[str, dict[int, int]]:
    """검토용 학습지 글: 문항에 번호를 붙이고, 번호 → 블록 위치를 돌려준다."""
    from .render import CIRCLED, _option_text
    from .schema import QUESTION_TYPES

    lines, where, num = [], {}, 0
    for i, b in enumerate(ws.blocks):
        if b.type in QUESTION_TYPES:
            num += 1
            where[num] = i
            body = re.sub(r"\[\[(.*?)\]\]", "(    )", b.body)  # 빈칸은 학생이 보는 대로
            head = f"{num}. [{BLOCK_TYPES[b.type][0]}] {b.title + ' ' if b.title else ''}{body}".strip()
            opts = "  ".join(f"{CIRCLED[k] if k < 10 else k + 1} {_option_text(o)}" for k, o in enumerate(b.items)) if b.type == "multiple_choice" else ""
            items = (" / ".join(re.sub(r"\[\[(.*?)\]\]", "(    )", it) for it in b.items)
                     if b.type in ("ox", "drill", "cornell") else "")
            lines.append(head + (f"\n   보기: {opts}" if opts else "") + (f"\n   항목: {items}" if items else "")
                         + f"\n   정답: {b.answer_text() or '(없음)'}" + (f"\n   해설: {b.solution}" if b.solution else ""))
        elif b.type == "diagram" and b.diagram:
            lines.append(f"[그림] {b.body} — {json.dumps(b.diagram, ensure_ascii=False)}")
        elif b.type in ("text", "reading", "concept", "table") and (b.body or b.items or b.rows):
            note_no = EXPLAIN_BASE + len([k for k in where if k > EXPLAIN_BASE]) + 1  # 설명 글도 틀린 내용이 있으면 고친다
            where[note_no] = i
            content = b.body or " / ".join(b.items) or " / ".join(" | ".join(r) for r in b.rows)
            lines.append(f"[설명 {note_no}] [{BLOCK_TYPES[b.type][0]}] {b.title} {content[:400]}".strip())
    return "\n".join(lines), where


def review_worksheet(*, api_key: str, ws: Worksheet, grade: int, subject: str, limits: str,
                     model: str = MODELS[0]) -> tuple[Worksheet, list[str]]:
    """만든 학습지를 AI가 문항마다 직접 풀어 보며 검토한다. 정답이 틀리면 고치고, 학년 범위를 벗어나거나
    애매한 문항은 그 문항만 다시 쓰게 한다. (고친 학습지, 사람이 읽을 검토 결과)를 돌려준다."""
    from concurrent.futures import ThreadPoolExecutor

    from . import verify
    from .render import CIRCLED, _option_text
    from .schema import QUESTION_TYPES

    text, where = _review_text(ws)
    if not any(k < EXPLAIN_BASE for k in where):
        return ws, []
    prompt = f"""너는 초등학교 {grade}학년 {subject} 학습지를 검토하는 꼼꼼한 교사다. JSON으로만 답한다.
아래 학습지의 문항을 하나씩 처음부터 직접 풀어 보고, 문제가 있는 문항만 알려 준다.

## 검토할 것
1. answer — 직접 푼 답과 학습지의 정답이 다르다. 바른 answer와 solution을 쓴다. 계산은 두 번 확인한다.
2. scope — '학년 범위'를 벗어난다: 아직 배우지 않은 개념·계산·용어를 쓰거나, 그 학년이 다루는 수의 범위를 넘는다.
   '이 학습지의 성취기준'이 다루는 내용은 범위 안이다 — 다음 학년 내용과 낱말이 겹쳐 보여도 scope로 지적하지 않는다.
3. unclear — 다음 중 하나:
   - 정답이 둘 이상이거나, 조건이 빠져 풀 수 없거나, 선택형 보기에 정답이 없거나 둘이다.
   - '위 대화·글·표·그림·수직선을 보고'처럼 자료를 가리키는데 학습지에 그 자료가 없다.
   - 그림(도식)의 수가 문항과 맞지 않거나, 그림이 정답을 그대로 보여 준다(예: 32 + 15를 묻는데 수 모형이 47을 보여 줌).
   - 정답이 문제 글에 그대로 드러나 있다(예: "'A' 소리가 나는 ant의 첫 글자는?").
4. fact — [설명 101]처럼 번호가 붙은 설명 글(개념·읽기 글·표)에 틀린 내용(잘못된 공식·뜻·사실·계산)이 있다. num에 그 번호를 쓴다.
   설명 글이 학년 범위 밖 용어를 쓰면 scope로 그 번호를 쓴다.
- 빈칸은 (    )로 보인다. 학습지의 '정답' 줄은 학생에게 보이지 않는다.
- 문제없는 문항은 쓰지 않는다. 말투·표현 취향은 지적하지 않는다. 확실한 것만 쓴다.
- 4학년까지는 약분을 배우지 않으므로 약분하지 않은 분수 답(예: 4/6)은 맞는 답이다.

## 학년 범위
{limits}

## 학습지
{text}
"""
    from google import genai

    client = genai.Client(api_key=api_key.strip())
    _notify("AI가 문항을 하나씩 다시 풀어 보며 검토하는 중…")
    raw, _ = _generate_with_retry(client, model, prompt, [], _REVIEW_SCHEMA)
    issues = [x for x in (_parse_json(raw).get("issues") or []) if isinstance(x, dict) and x.get("num") in where]
    # 문항 절반 넘게 '범위 밖'이라면 성취기준 자체를 범위 밖으로 잘못 본 것이다 (예: 4학년 '문장의 짜임'을 5학년 '문장 성분'으로)
    n_questions = sum(1 for k in where if k < EXPLAIN_BASE)
    if sum(1 for x in issues if x.get("problem") == "scope") > max(2, n_questions // 2):
        issues = [x for x in issues if x.get("problem") != "scope"]
    # 자료를 가리키는데 자료가 없는 문항은 AI가 놓쳐도 프로그램이 찾는다
    flagged = {x["num"] for x in issues}
    for n, note in verify.missing_sources(ws) + (verify.answer_shown(ws) if "영어" in subject else []) + verify.figure_issues(ws):
        if n not in flagged:
            issues.append({"num": n, "problem": "unclear", "note": note})
            flagged.add(n)

    notes: list[str] = []
    rewrite = []
    for x in issues:
        b = ws.blocks[where[x["num"]]]
        note = str(x.get("note", "")).strip()
        if x["num"] > EXPLAIN_BASE:  # 설명 글은 틀린 내용(fact)과 학년 범위 밖 용어(scope)만 바로잡는다
            if x.get("problem") in ("fact", "scope"):
                rewrite.append((x["num"], note or "설명의 틀린 내용을 바로잡는다"))
        elif x.get("problem") == "answer" and str(x.get("answer", "")).strip() and b.type in ("short_answer", "essay", "multiple_choice", "ox"):
            new = str(x["answer"]).strip()
            if b.type == "multiple_choice" and new[:1] in CIRCLED and CIRCLED.index(new[0]) < len(b.items):
                new = f"{new[0]} {_option_text(b.items[CIRCLED.index(new[0])])}"
            if re.search(r"고칠 (?:필요|것이?) 없|정답(?:이)? 없|답이 없", new):  # 문제 자체가 성립하지 않으면 다시 쓴다
                rewrite.append((x["num"], note or "문제의 전제가 틀려 답이 없다"))
                continue
            if re.sub(r"\s+", "", new) == re.sub(r"\s+", "", b.answer):  # 정답은 맞고 해설만 틀림
                notes.append(f"{x['num']}번 해설을 바로잡았어요" + (f" ({note})" if note else ""))
            else:
                notes.append(f"{x['num']}번 정답 {b.answer or '(없음)'} → {new}" + (f" ({note})" if note else ""))
            b.answer, b.solution = new, str(x.get("solution") or b.solution).strip()
        else:  # 범위를 벗어났거나 애매한 문항, 빈칸·연습 문제의 정답 → 그 문항만 다시 쓴다
            rewrite.append((x["num"], note or "문항을 다시 확인해 고친다"))

    context = {"title": ws.title, "subject": ws.subject, "unit": ws.unit, "goals": ws.goals, "level": f"초등 {grade}학년"}

    def figure_of(n: int) -> int | None:
        """문항 바로 앞(사이에 다른 문항 없이)에 있는 도식의 위치."""
        i = where[n] - 1
        while i >= 0 and ws.blocks[i].type not in QUESTION_TYPES:
            if ws.blocks[i].type == "diagram":
                return i
            i -= 1
        return None

    def redo(job):
        n, note = job
        b = ws.blocks[where[n]]
        fig = figure_of(n) if re.search(r"그림|도식|수 모형|수직선|시계|막대|그래프", note) else None
        out = []
        others = []
        if n > EXPLAIN_BASE:  # 설명 글은 틀린 곳만 바로잡는다
            try:
                return n, note, [(where[n], revise_block(api_key=api_key, block=b.model_dump(), context=context, model=model,
                                                        instruction=f"검토 결과: {note}\n이 설명에서 틀린 내용이나 학년 범위 밖의 용어만 바로잡고 나머지는 그대로 둔다.\n{limits}"))]
            except AnalyzeError:
                return n, note, []
        for k, i in where.items():
            if k != n and k < EXPLAIN_BASE:
                shown = re.sub(r"\[\[(.*?)\]\]", "(  )", ws.blocks[i].body)[:80]
                others.append(f"- {shown} (정답: {ws.blocks[i].answer_text()[:20]})")
        instruction = (f"검토 결과: {note}\n이 문제를 고친다. 같은 성취기준·같은 종류로, 아래 학년 범위를 꼭 지키고, "
                       "정답이 하나로 분명하게. 계산은 두 번 확인한다. 문제를 푸는 데 필요한 대화·글·자료는 body 안에 함께 쓴다"
                       "(학습지에 없는 자료를 가리키지 않는다). 정답이 문제 글에 드러나지 않게 한다"
                       "(영어 철자를 묻는 문제에 그 영어 단어를 보여 주지 않는다).\n"
                       "다른 문항과 내용·정답이 겹치지 않게 한다. 다른 문항들:\n" + "\n".join(others) + "\n" + limits)
        try:
            out.append((where[n], revise_block(api_key=api_key, block=b.model_dump(), instruction=instruction,
                                               context=context, model=model)))
            if fig is not None:  # 그림이 문제면 그림도 문항에 맞게 (정답을 그대로 보여 주지 않게)
                fig_instr = (f"검토 결과: {note}\n아래 문항에 맞게 이 도식을 고친다. 도식이 정답을 그대로 보여 주면 안 된다"
                             f"(문제의 수를 보여 주거나 정답 부분을 가린다).\n문항: {out[0][1].body}\n정답: {out[0][1].answer}")
                out.append((fig, revise_block(api_key=api_key, block=ws.blocks[fig].model_dump(), instruction=fig_instr,
                                              context=context, model=model)))
        except AnalyzeError:
            pass
        return n, note, out

    done: dict[int, str] = {}  # 고쳐 쓴 문항 번호 → 처음 찾은 문제
    for round_no in range(2):  # 고쳐 쓴 문항도 프로그램 검사를 다시 하고, 또 걸리면 한 번 더 고친다
        if not rewrite:
            break
        _notify(f"검토에서 찾은 문항 {len(rewrite)}개를 고치는 중…" + (" (한 번 더)" if round_no else ""))
        with ThreadPoolExecutor(max_workers=4) as pool:
            for n, note, out in pool.map(redo, rewrite):
                for i, new in out:
                    ws.blocks[i] = new
                if out:
                    done.setdefault(n, note + (" (그림도 함께)" if len(out) > 1 else ""))
                else:
                    notes.append(f"{n}번 확인이 필요해요: {note}" if n < EXPLAIN_BASE else f"설명 글 확인이 필요해요: {note}")
        ws = _fix_choice_answers(ws)
        verify.strip_numbers(ws)
        again = dict(verify.missing_sources(ws) + (verify.answer_shown(ws) if "영어" in subject else []) + verify.figure_issues(ws))
        rewrite = [(n, f"{again[n]} — 앞에서 고쳤는데도 그대로예요. 이번에는 반드시 고친다") for n in again if n in done]
    def name(n: int) -> str:
        return f"{n}번 문항" if n < EXPLAIN_BASE else f"설명 글({BLOCK_TYPES[ws.blocks[where[n]].type][0]})"

    for n, note in sorted(done.items()):
        notes.append(f"{name(n)}을 고쳐 썼어요 — {note}")
    for n, note in rewrite:  # 두 번 고쳐도 남은 것
        notes.append(f"{name(n)} 확인이 필요해요: {note.split(' — ')[0]}")
    notes += verify.check(ws, reduce=grade >= 5)  # 고친 문항도 계산 정답을 한 번 더 검산
    return ws, notes
