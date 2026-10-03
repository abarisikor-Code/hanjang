"""한장 — 누구나 쉽게 만드는 일관된 A4 학습지 (Streamlit 화면).

실행: python -m streamlit run app.py

화면 흐름 (컴퓨터가 익숙하지 않은 학부모·선생님 기준: 한 화면에 한 가지씩)
- 🏠 처음 화면: 큰 카드 3개 — 한 과목 학습지 / 여러 과목 융합 과제 / 내 자료로 만들기
- 📘 한 과목: ① 학년·과목 → ② 배울 내용 → ③ 꾸미기 → ④ 완성
- 🔗 융합 과제: ① 학년·과목들 → ② 배울 내용(선택) → ③ 과제·꾸미기 → ④ 완성
- 📂 내 자료: 파일·글로 / 학습지 따라 만들기 / 빈 학습지 / 저장한 학습지 열기
- ✅ 완성: 큰 미리보기 + 저장·인쇄·다시 만들기, 옆에서 내용 고치기(✨ AI 도움)·모양 바꾸기
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from worksheet_maker import curriculum, diagrams, extract, forms, images, kinds, llm
from worksheet_maker.render import LEVELS, render
from worksheet_maker.saved import LoadError, unpack
from worksheet_maker.schema import BLOCK_TYPES, IMAGE_SIZES, QUESTION_TYPES, Block, Worksheet
from worksheet_maker.theme import THEME_OPTIONS, Theme, list_themes, save_theme

ENV_FILE = Path(__file__).parent / ".env"
load_dotenv(ENV_FILE)
SAMPLE = Path(__file__).parent / "examples" / "sample.json"
# 이 컴퓨터에서 혼자 쓰는 실행(run.bat·포터블)만 HANJANG_LOCAL=1을 준다. 그 밖(인터넷 배포)은 여러 사람이 한 서버를 쓰므로
# 키를 서버 .env에서 읽지 않고, 양식·모양을 서버 디스크에 저장하지 않는다(다른 사람에게 보이지 않게) — 내려받기로 대신한다.
LOCAL = os.getenv("HANJANG_LOCAL") == "1"
KEY_URL = "https://aistudio.google.com/apikey"


def _remember_key(key: str) -> None:
    """이 컴퓨터의 .env에 키를 적어 둔다 (LOCAL일 때만). 다음 실행부터 자동으로 채워진다."""
    lines = [ln for ln in (ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else [])
             if not ln.startswith("GEMINI_API_KEY=")]
    if key:
        lines.append(f"GEMINI_API_KEY={key}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["GEMINI_API_KEY"] = key
    st.session_state["w_api_key"] = key


def key_help() -> None:
    """API 키 받는 방법 (처음 쓰는 분용)."""
    st.markdown(
        f"1. [{KEY_URL}]({KEY_URL}) 에 들어가 구글 계정으로 로그인합니다.\n"
        "2. 처음이면 약관 동의 창이 나옵니다. 확인하고 넘어갑니다.\n"
        "3. **Create API key**(API 키 만들기)를 누르고, 만들어진 키(AIza로 시작)를 복사합니다.\n"
        "4. 왼쪽 **⚙️ 설정**의 'Gemini API 키' 칸에 붙여 넣습니다.\n\n"
        "무료로 쓸 수 있어요. 하루에 만들 수 있는 양에 한도가 있어서, 많이 만든 날은 다음 날 다시 채워집니다.")

_ICON = Path(__file__).parent / "assets" / "icon.png"  # 바탕화면·작업 표시줄·브라우저 탭에 같은 아이콘
st.set_page_config(page_title="한장 · 학습지 만들기", page_icon=str(_ICON) if _ICON.exists() else "📝", layout="wide")

# 앱 화면(학습지 아님) 꾸밈: 버튼·글자를 조금 크게, 카드를 보기 좋게
st.markdown(
    """<style>
.block-container { padding-top: 2.2rem; max-width: 1350px; }
div[data-testid="stButtonGroup"] button { min-height: 2.6rem; padding: .35rem 1.1rem; }
div[data-testid="stButtonGroup"] button p { font-size: 1.08rem; }
div[data-testid="stButton"] button p { font-size: 1rem; }
.hj-step { display:flex; gap:.4rem; flex-wrap:wrap; margin:.2rem 0 1.2rem; }
.hj-step span { padding:.35rem .9rem; border-radius:99px; background:#eef0f4; color:#667; font-size:.95rem; }
.hj-step span.on { background:#ff4b4b; color:#fff; font-weight:700; }
.hj-step span.done { background:#ffe3e3; color:#c33; }
.hj-card-emoji { font-size: 2.6rem; line-height: 1.1; }
</style>""",
    unsafe_allow_html=True,
)

# 구성요소별 편집 칸: type → [(필드, 라벨)]
FIELDS: dict[str, list[tuple[str, str]]] = {
    "section": [("title", "소제목")],
    "text": [("title", "작은 제목 (선택)"), ("body", "내용")],
    "concept": [("title", "상자 이름 (비우면 '핵심 개념')"), ("items", "용어: 설명 (한 줄에 하나)")],
    "reading": [("title", "자료 이름 (비우면 '읽기 자료')"), ("body", "내용"), ("source", "출처")],
    "quote": [("body", "인용문"), ("source", "말한 사람·출처")],
    "table": [("title", "표 제목 (선택)"), ("rows", "표 내용 (칸은 | 로 구분, 첫 줄은 머리글)")],
    "activity": [("title", "활동 이름"), ("body", "안내문"), ("items", "진행 순서 (한 줄에 하나)")],
    "short_answer": [("body", "질문"), ("lines", "답 줄 수"), ("answer", "정답 (선택)"), ("solution", "해설 (선택)")],
    "essay": [("body", "질문"), ("lines", "답 줄 수"), ("answer", "예시 답안 (선택)"), ("solution", "해설 (선택)")],
    "multiple_choice": [("body", "질문"), ("items", "선택지 (한 줄에 하나, 번호 없이)"), ("answer", "정답 (예: ③ 눈이 내린다)"), ("solution", "해설 (선택)")],
    "fill_blank": [("body", "문장 — 빈칸 자리는 [[정답]]처럼 쓰세요"), ("solution", "해설 (선택)")],
    "ox": [("title", "지시문 (비우면 기본 문구)"), ("items", "진술문 (한 줄에 하나)"), ("answer", "정답 (예: (1) O (2) X)"), ("solution", "해설 (선택)")],
    "drawing": [("body", "지시문"), ("lines", "상자 높이 (줄 단위)"), ("solution", "해설 (선택)")],
    "diagram": [("diagram", "도식"), ("size", "크기"), ("body", "그림 설명 (그림 아래 [그림 1]과 함께 표시)")],
    "image": [("image", "그림 파일"), ("size", "크기"), ("body", "그림 설명 (그림 아래 [그림 1]과 함께 표시)"), ("source", "출처 (선택)")],
    "reflection": [("title", "제목 (비우면 '스스로 점검하기')"), ("items", "점검 항목 (한 줄에 하나)")],
    "drill": [("title", "지시문 (예: 계산해 보세요.)"), ("items", "문제 (한 줄에 하나, 정답 자리는 [[정답]]처럼)")],
    "cornell": [("title", "소주제"), ("items", "핵심어: 설명 (한 줄에 하나, 빈칸은 [[정답]]처럼)")],
    "bingo": [("title", "제목 (비우면 '정답 빙고')"), ("items", "헷갈리는 오답 (한 줄에 하나) — 정답은 문항에서 자동으로 들어가요")],
}
TYPE_LABEL = {k: v[0] for k, v in BLOCK_TYPES.items()}
LEVEL_CHOICES = {"lower": "초등 1~2학년", "elementary": "초등 3~6학년", "middle": "중학교", "high": "고등학교"}


# ----- 상태 -----
def _new_id() -> str:
    return uuid4().hex[:8]


def set_worksheet(ws: Worksheet) -> None:
    st.session_state.pop("review_notes", None)  # 검토 결과는 그 학습지를 만든 직후에만 보여 준다
    data = ws.model_dump()
    for b in data["blocks"]:
        b["_id"] = _new_id()
    st.session_state.ws = data
    st.session_state.rev += 1  # 제목 등 입력칸을 새 값으로 다시 그리게 한다


def set_theme(theme: Theme) -> None:
    st.session_state.theme = theme.model_dump()
    st.session_state.theme_rev += 1


def current_worksheet() -> Worksheet:
    return Worksheet.model_validate(st.session_state.ws)


def current_theme() -> Theme:
    return Theme.model_validate(st.session_state.theme)


def current_form() -> forms.Form | None:
    """내 학습지 양식 (없으면 기본 모양으로 조판)."""
    data = st.session_state.get("form")
    return forms.Form.model_validate(data) if data else None


def set_form(form: forms.Form | None) -> None:
    st.session_state.form = form.model_dump() if form else None
    st.session_state.form_rev = st.session_state.get("form_rev", 0) + 1  # 칸 고치기 입력칸을 새로 그린다


ss = st.session_state
if "ws" not in ss:
    ss.rev = ss.theme_rev = 0
    ss.open_id = None
    # 화면을 옮겨 다녀도 사라지지 않게, 위젯과 따로 기억하는 값들
    ss.doc_level = "elementary"  # 글자 크기·답 칸을 정하는 학교급
    ss.footer_text = ""
    ss.answers_on = True
    ss.wkind = "standard"  # 학습지 유형 (kinds.KINDS): 3단계에서 고르고 다음 학습지에도 이어 쓴다
    ss.form, ss.form_rev = None, 0  # 내 학습지 양식: 새 학습지를 만들어도 끌 때까지 계속 쓴다
    ss.grade, ss.subject, ss.fusion_subjects = 3, "국어", []
    # 주소 끝 ?mode=single|fusion|tools 로 바로 그 화면을 열 수 있다 (예전 parent/teacher도 받음).
    _q = st.query_params.get("mode", "")
    _q = {"parent": "single", "teacher": "tools"}.get(_q, _q)
    ss.page = {"single": "s1", "fusion": "f1", "tools": "tools"}.get(_q, "home")
    set_worksheet(Worksheet(title="새 학습지"))
    set_theme(Theme())


def go(page: str) -> None:
    ss.page = page


def _sync(target: str, widget: str) -> None:
    """위젯 값을 화면이 바뀌어도 남는 칸에 옮겨 둔다."""
    ss[target] = ss[widget]


def md(text: object) -> str:
    """화면 글(마크다운)에서 '1~2장 … 5~20초'처럼 ~가 두 번 나오면 그 사이가 취소선이 되므로 ~를 글자로 바꾼다."""
    return str(text).replace("~", "\\~")


@contextmanager
def ai_status(what: str):
    """AI 작업 중 진행 상황(어느 모델로 만드는 중인지, 모델을 바꿨는지)을 보여 준다."""
    box = st.status(md(f"{what}… 보통 10~30초, 서버가 붐비면 2~3분까지 걸릴 수 있어요"))
    token = llm.progress.set(lambda msg: box.update(label=md(f"{what} — {msg}")))
    try:
        yield box
    except Exception:
        box.update(label=f"{what} — 실패", state="error")
        raise
    else:
        box.update(label=f"{what} — 완료", state="complete")
    finally:
        llm.progress.reset(token)


def _focus(bid: str) -> None:
    ss.open_id = bid


# =====================================================================
# 사이드바: 처음으로 · 설정 (자주 안 쓰는 것은 접어 둔다)
# =====================================================================
with st.sidebar:
    st.markdown("## 📝 한장")
    st.caption("누구나 쉽게 만드는 A4 학습지")
    st.button("🏠 처음 화면으로", width="stretch", on_click=go, args=("home",))
    if ss.ws["blocks"] and ss.page != "result":
        st.button("✏️ 만들던 학습지로", width="stretch", on_click=go, args=("result",))
    st.divider()
    env_key = os.getenv("GEMINI_API_KEY", "").strip() if LOCAL else ""  # 인터넷판은 서버 키를 절대 쓰지 않는다
    if "w_api_key" not in ss:
        ss.w_api_key = env_key
    with st.expander("⚙️ 설정", expanded=not ss.w_api_key):
        api_key = st.text_input(
            "Gemini API 키", type="password", key="w_api_key",
            help=f"{KEY_URL} 에서 무료로 받을 수 있어요. 받는 방법은 아래 '🔑 키 받는 방법'을 보세요.",
        ).strip()
        if LOCAL:
            if api_key and api_key != env_key:
                st.button("💾 이 컴퓨터에 키 기억하기", on_click=_remember_key, args=(api_key,), width="stretch",
                          help="다음에 열 때 키를 다시 넣지 않아도 돼요. 이 컴퓨터를 다른 사람과 같이 쓰면 누르지 마세요.")
            elif env_key:
                st.caption("🔑 이 컴퓨터에 기억해 둔 키를 쓰고 있어요.")
                st.button("기억한 키 지우기", on_click=_remember_key, args=("",), width="stretch")
        elif api_key:
            st.caption("🔒 키는 이 브라우저 창에서만 쓰이고 서버에 저장되지 않아요. 창을 닫으면 지워져요.")
        if not api_key:
            with st.popover("🔑 키 받는 방법", width="stretch"):
                key_help()
        model = st.selectbox(
            "AI 모델", llm.MODELS, format_func=lambda m: llm.MODEL_LABELS.get(m, m),
            help="고른 모델이 붐비거나 오래 걸리면 다른 모델로 자동으로 바꿔 만듭니다.",
        )
    st.caption("도움말: 막히면 🏠 처음 화면으로 돌아가 다시 시작해도 만들던 학습지는 남아 있어요.")

level = ss.doc_level
show_answers = ss.answers_on
footer = ss.footer_text


def step_bar(steps: list[str], current: int) -> None:
    parts = []
    for i, name in enumerate(steps, 1):
        cls = "on" if i == current else ("done" if i < current else "")
        parts.append(f'<span class="{cls}">{"✓ " if i < current else ""}{i}. {name}</span>')
    st.html('<div class="hj-step">' + "".join(parts) + "</div>")


# =====================================================================
# 고치기 (구성요소 편집기)
# =====================================================================
def _text_value(b: dict, field: str) -> str:
    if field == "items":
        return "\n".join(b.get("items", []))
    if field == "rows":
        return "\n".join(" | ".join(r) for r in b.get("rows", []))
    return b.get(field, "")


def _store(b: dict, field: str, value) -> None:
    if field == "items":
        b["items"] = [line.strip() for line in value.splitlines() if line.strip()]
    elif field == "rows":
        b["rows"] = [[c.strip() for c in line.split("|")] for line in value.splitlines() if line.strip()]
    else:
        b[field] = value


def _move(i: int, d: int) -> None:
    blocks = ss.ws["blocks"]
    j = i + d
    if 0 <= j < len(blocks):
        blocks[i], blocks[j] = blocks[j], blocks[i]
        ss.open_id = blocks[j]["_id"]


def _copy(i: int) -> None:
    b = {**ss.ws["blocks"][i], "_id": _new_id()}
    ss.ws["blocks"].insert(i + 1, b)
    ss.open_id = b["_id"]


def _delete(i: int) -> None:
    ss.ws["blocks"].pop(i)


def _set_image(bid: str) -> None:
    """그림 파일을 올리면 인쇄에 알맞은 크기로 줄여 학습지에 넣는다."""
    f = ss.get(f"{bid}_imgup")
    b = next((x for x in ss.ws["blocks"] if x["_id"] == bid), None)
    ss.open_id = bid
    if f is None or b is None:
        return
    try:
        b["image"] = images.to_data_uri(f.getvalue())
    except images.ImageError as e:
        ss[f"{bid}_imgerr"] = str(e)


def _clear_image(bid: str) -> None:
    for x in ss.ws["blocks"]:
        if x["_id"] == bid:
            x["image"] = ""
    ss.pop(f"{bid}_imgup", None)  # 올렸던 파일 칸도 비운다
    ss.open_id = bid


def _diagram_editor(b: dict, bid: str) -> None:
    """도식: 종류를 고르면 그 종류에 맞는 칸이 나온다. 그림은 오른쪽 미리보기에서 바로 보인다."""
    spec = b.get("diagram") or {}
    kinds = list(diagrams.KINDS)
    kind = st.selectbox(
        "그림 종류", kinds, index=kinds.index(spec["kind"]) if spec.get("kind") in kinds else 0,
        format_func=lambda k: f"{diagrams.KINDS[k][0]} — {diagrams.KINDS[k][1].split('.')[0]}",
        key=f"{bid}_dg_kind", on_change=_focus, args=(bid,),
    )
    defaults = diagrams.KINDS[kind][2]
    cur = {**defaults, **(spec if spec.get("kind") == kind else {})}
    new = {"kind": kind}
    cols = st.columns(2)
    for n, (name, default) in enumerate(defaults.items()):
        label = diagrams.PARAM_LABELS.get(name, name)
        key = f"{bid}_dg_{kind}_{name}"
        col = cols[n % 2]
        if name in diagrams.CHOICES and not isinstance(default, list):
            opts = list(diagrams.CHOICES[name])
            if name == "item" and cur[name] not in opts:
                opts.append(cur[name])  # 이모지 그림
            new[name] = col.selectbox(label, opts, index=opts.index(cur[name]) if cur[name] in opts else 0,
                                      format_func=lambda v, n=name: diagrams.CHOICES[n].get(v, v),
                                      key=key, on_change=_focus, args=(bid,))
        elif isinstance(default, bool):
            new[name] = col.checkbox(label, value=bool(cur[name]), key=key, on_change=_focus, args=(bid,))
        elif isinstance(default, int):
            new[name] = col.number_input(label, value=int(cur[name]), step=1, key=key, on_change=_focus, args=(bid,))
        elif isinstance(default, list):
            text = col.text_input(label, value=", ".join(str(x).removesuffix(".0") for x in cur[name]), key=key,
                                  on_change=_focus, args=(bid,))
            new[name] = [t.strip() for t in text.split(",") if t.strip()]
        else:
            new[name] = col.text_input(label, value=str(cur[name]), key=key, on_change=_focus, args=(bid,))
    if kind == "groups":
        st.caption("모양에 🌰 ⚽ 🍎 같은 그림(이모지)을 쓰려면 아래 칸에 붙여 넣으세요.")
        emo = st.text_input("그림으로 바꾸기 (이모지 하나)", value=cur["item"] if len(str(cur["item"])) <= 3 else "",
                            key=f"{bid}_dg_groups_emoji", on_change=_focus, args=(bid,))
        if emo.strip():
            new["item"] = emo.strip()
    b["diagram"] = diagrams.normalize(new)
    if not b["diagram"]:
        st.warning("이 값으로는 그릴 수 없어요. 숫자와 항목 수를 확인해 주세요.")


def _add(kind: str) -> None:
    b = {
        "type": kind, "title": "", "body": "", "items": [], "source": "", "rows": [], "lines": 0,
        "image": "", "size": "m", "_id": _new_id(),
    }
    if kind == "table":
        b["rows"] = [["항목", "내용"], ["", ""]]
    if kind == "diagram":
        b["diagram"] = diagrams.normalize({"kind": "groups"})
    ss.ws["blocks"].append(b)
    ss.open_id = b["_id"]


def _start_blank() -> None:
    set_worksheet(Worksheet(title="새 학습지"))
    ss.last_req = None
    ss.page = "result"


def _load_sample() -> None:
    set_worksheet(Worksheet.model_validate_json(SAMPLE.read_text(encoding="utf-8")))


def _open_saved(widget_key: str) -> None:
    """저장한 HTML(또는 예전 JSON)을 올리면 내용·모양·학교급·꼬리말을 그대로 되살린다."""
    f = ss.get(widget_key)
    if f is None:
        return
    try:
        saved = unpack(f.name, f.getvalue())
    except LoadError as e:
        ss.open_msg = ("error", str(e))
        return
    set_worksheet(saved.worksheet)
    if saved.theme:
        set_theme(saved.theme)
    if saved.level in LEVELS:
        ss.doc_level = saved.level
    if saved.footer is not None:
        ss.footer_text = saved.footer
    if saved.show_answers is not None:
        ss.answers_on = saved.show_answers
    set_form(saved.form)  # 양식에 넣어 저장한 파일이면 그 양식도 되살린다
    ss.last_req = None
    ss.p_msg = ("success", f"'{saved.worksheet.title}'을(를) 열었어요. 이어서 고치고 저장하세요.")
    ss.page = "result"


def _snippet(b: dict) -> str:
    """고치기 목록에서 구성요소를 알아보기 쉽게 내용 앞부분을 보여 준다."""
    t = b.get("title") or b.get("body") or (b.get("items") or [""])[0]
    if not t and b["type"] == "diagram" and b.get("diagram"):
        t = diagrams.KINDS[b["diagram"]["kind"]][0]
    t = re.sub(r"\s+", " ", re.sub(r"\[\[(.*?)\]\]", "□", t)).strip()
    return md(t[:30] + "…" if len(t) > 30 else t)


def _context() -> dict:
    ws = ss.ws
    who = f"초등 {ss.grade}학년" if ss.get("last_req") else LEVELS[level]["label"]
    return {"title": ws["title"], "subject": ws["subject"], "unit": ws["unit"], "goals": ws["goals"], "level": who}


def _describe(b: dict) -> str:
    """AI 추천안을 읽기 쉬운 글로."""
    lines = [f"**{TYPE_LABEL.get(b['type'], b['type'])}**"]
    if b.get("title"):
        lines.append(f"제목: {b['title']}")
    if b.get("body"):
        lines.append(re.sub(r"\[\[(.*?)\]\]", r"(빈칸: \1)", b["body"]))
    for k, it in enumerate(b.get("items") or [], 1):
        lines.append(f"{'①②③④⑤⑥⑦⑧⑨⑩'[k - 1] if b['type'] == 'multiple_choice' and k <= 10 else '-'} {it}")
    if b.get("diagram"):
        lines.append(f"그림: {diagrams.KINDS[b['diagram']['kind']][0]} {json.dumps({k: v for k, v in b['diagram'].items() if k != 'kind'}, ensure_ascii=False)}")
    if b.get("answer"):
        lines.append(f"✅ 정답: {b['answer']}")
    if b.get("solution"):
        lines.append(f"💡 해설: {b['solution']}")
    return md("  \n".join(lines))


def _apply_suggestion(bid: str) -> None:
    sugg = ss.pop(f"{bid}_sugg", None)
    ss.pop(f"{bid}_sugg_instr", None)
    if not sugg:
        return
    for blk in ss.ws["blocks"]:
        if blk["_id"] == bid:
            blk.clear()
            blk.update(sugg)
            blk["_id"] = bid
    # 이 구성요소의 입력칸들이 새 값으로 다시 그려지도록 기억된 입력값을 지운다.
    for k in [k for k in list(ss.keys()) if isinstance(k, str) and k.startswith(f"{bid}_") and k != f"{bid}_exp"]:
        del ss[k]
    ss.open_id = bid


def _drop_suggestion(bid: str) -> None:
    ss.pop(f"{bid}_sugg", None)
    ss.pop(f"{bid}_sugg_instr", None)


def _ask_ai(b: dict, bid: str, instruction: str) -> None:
    try:
        with ai_status("AI가 고칠 안을 만드는 중"):
            new = llm.revise_block(api_key=api_key, block=b, instruction=instruction, context=_context(), model=model)
    except llm.AnalyzeError as e:
        st.error(str(e))
        return
    ss[f"{bid}_sugg"] = new.model_dump()
    ss[f"{bid}_sugg_instr"] = instruction
    ss.open_id = bid
    st.rerun()


def _ai_help(b: dict, bid: str) -> None:
    """구성요소마다 '✨ AI 도움': 바로 바꾸지 않고 추천안을 먼저 보여 준 뒤 적용을 고르게 한다."""
    if b["type"] == "image":
        return
    sugg = ss.get(f"{bid}_sugg")
    if sugg:
        with st.container(border=True):
            st.markdown("**✨ AI 추천안** — 마음에 들면 '적용'을 눌러 주세요")
            st.markdown(_describe(sugg))
            c1, c2, c3 = st.columns(3)
            c1.button("✅ 적용", key=f"{bid}_sg_ok", type="primary", width="stretch", on_click=_apply_suggestion, args=(bid,))
            if c2.button("🔄 다른 안", key=f"{bid}_sg_again", width="stretch"):
                _ask_ai(b, bid, ss.get(f"{bid}_sugg_instr", "") + "\n(앞 추천과 다른 새로운 안으로)")
            c3.button("취소", key=f"{bid}_sg_no", width="stretch", on_click=_drop_suggestion, args=(bid,))
        return
    is_question = b["type"] in QUESTION_TYPES
    actions = ["easier", "harder", "another", "polish"] + (["answer"] if is_question else [])
    if b["type"] in ("section",):
        actions = ["polish", "another"]
    with st.popover("✨ AI 도움 — 쉽게·어렵게·다른 문제로", width="stretch"):
        st.caption("AI가 고친 안을 먼저 보여 드려요. 마음에 들 때만 바뀌어요.")
        cols = st.columns(2)
        for n, key in enumerate(actions):
            label, instr = llm.REVISE_ACTIONS[key]
            if key == "another" and not is_question:
                label = "🔄 다른 내용으로"
            if cols[n % 2].button(label, key=f"{bid}_ai_{key}", width="stretch"):
                _ask_ai(b, bid, instr)
        with st.form(f"{bid}_ai_form", border=False):
            wish = st.text_input("직접 부탁하기", key=f"{bid}_ai_wish",
                                 placeholder="예: 숫자를 100보다 작게 / 선택형으로 바꿔 줘 / 공룡이 나오게")
            asked = st.form_submit_button("부탁하기", width="stretch")
        if asked and wish.strip():
            _ask_ai(b, bid, wish)


def block_list() -> None:
    """내용 고치기: 구성요소 목록. 누르면 펼쳐져서 고치고, ✨ AI 도움으로 쉽게 바꾼다."""
    ws = ss.ws
    st.caption("고칠 칸을 누르세요. 글을 바꾸면 오른쪽 학습지에 바로 반영돼요. 어려우면 **✨ AI 도움**을 눌러 보세요.")
    if not ws["blocks"]:
        st.info("아직 내용이 없어요. 아래에서 넣을 것을 골라 ＋ 추가를 누르거나, '📝 제목·목표' 탭에서 **✨ AI로 내용 채우기**를 해 보세요.")

    for i, b in enumerate(ws["blocks"]):
        bid = b["_id"]
        snip = _snippet(b)
        mark = " ✨" if f"{bid}_sugg" in ss else ""
        with st.expander(f"{i + 1}. {TYPE_LABEL[b['type']]}" + (f" · {snip}" if snip else "") + mark,
                         expanded=(bid == ss.open_id), key=f"{bid}_exp"):
            _ai_help(b, bid)
            for field, label in FIELDS[b["type"]]:
                key = f"{bid}_{field}"
                if field == "diagram":
                    _diagram_editor(b, bid)
                elif field == "image":
                    if b.get("image"):
                        st.image(images.data_uri_bytes(b["image"]), width=220)
                        st.button("그림 빼기", key=f"{bid}_imgclr", on_click=_clear_image, args=(bid,))
                    st.file_uploader(
                        "그림 파일 넣기 (사진·그림)" if not b.get("image") else "다른 그림으로 바꾸기", type=images.ACCEPT,
                        key=f"{bid}_imgup", on_change=_set_image, args=(bid,),
                    )
                    if f"{bid}_imgerr" in ss:
                        st.error(ss.pop(f"{bid}_imgerr"))
                elif field == "size":
                    sizes = list(IMAGE_SIZES)
                    b["size"] = st.radio(
                        label, sizes, index=sizes.index(b.get("size", "m")), horizontal=True,
                        format_func=lambda k: IMAGE_SIZES[k][0], key=key, on_change=_focus, args=(bid,),
                    )
                elif field == "lines":
                    b["lines"] = st.number_input(
                        label, min_value=0, max_value=20, value=int(b.get("lines") or 0),
                        key=key, help="0이면 기본값", on_change=_focus, args=(bid,),
                    )
                elif field in ("body", "items", "rows"):
                    height = 150 if field == "body" and b["type"] in ("reading", "text") else 100
                    _store(b, field, st.text_area(label, value=_text_value(b, field), key=key, height=height, on_change=_focus, args=(bid,)))
                else:
                    _store(b, field, st.text_input(label, value=_text_value(b, field), key=key, on_change=_focus, args=(bid,)))
            with st.expander("종류 바꾸기", expanded=False):
                b["type"] = st.selectbox(
                    "이 칸의 종류", list(BLOCK_TYPES), index=list(BLOCK_TYPES).index(b["type"]),
                    format_func=lambda k: f"{TYPE_LABEL[k]} — {BLOCK_TYPES[k][1]}",
                    key=f"{bid}_type", on_change=_focus, args=(bid,), label_visibility="collapsed",
                )
            c1, c2, c3, c4 = st.columns(4)
            c1.button("▲ 위로", key=f"{bid}_up", on_click=_move, args=(i, -1), disabled=i == 0, width="stretch")
            c2.button("▼ 아래로", key=f"{bid}_down", on_click=_move, args=(i, 1), disabled=i == len(ws["blocks"]) - 1, width="stretch")
            c3.button("복사", key=f"{bid}_copy", on_click=_copy, args=(i,), width="stretch")
            c4.button("🗑 삭제", key=f"{bid}_del", on_click=_delete, args=(i,), width="stretch")

    with st.container(border=True):
        st.markdown("**＋ 새 칸 넣기**")
        c1, c2 = st.columns([3, 1])
        kind = c1.selectbox(
            "넣을 것", list(BLOCK_TYPES), format_func=lambda k: f"{TYPE_LABEL[k]} — {BLOCK_TYPES[k][1]}",
            label_visibility="collapsed", key="add_kind",
        )
        c2.button("＋ 추가", on_click=_add, args=(kind,), type="primary", width="stretch")


def header_panel() -> None:
    """제목·과목·단원·학습 목표·꼬리말 + (내용이 비었거나 직접 만들 때) AI로 내용 채우기."""
    ws = ss.ws
    r = ss.rev
    ws["title"] = st.text_input("학습지 제목", value=ws["title"], key=f"title_{r}")
    c1, c2, c3 = st.columns([1, 2, 1])
    ws["subject"] = c1.text_input("과목", value=ws["subject"], key=f"subject_{r}")
    ws["unit"] = c2.text_input("단원", value=ws["unit"], key=f"unit_{r}")
    ws["lesson"] = c3.text_input("차시", value=ws["lesson"], key=f"lesson_{r}", placeholder="3차시")
    goals = st.text_area("학습 목표 (한 줄에 하나)", value="\n".join(ws["goals"]), key=f"goals_{r}", height=80)
    ws["goals"] = [g.strip() for g in goals.splitlines() if g.strip()]
    st.text_input("꼬리말 (쪽 아래 왼쪽, 선택)", value=ss.footer_text, key="w_footer",
                  placeholder="예: ○○초등학교 3학년 / 우리 집 수학 공부", on_change=_sync, args=("footer_text", "w_footer"))
    ai_fill_panel(ws)


def ai_fill_panel(ws: dict) -> None:
    """머리말을 보고 AI가 구성요소를 채운다."""
    with st.expander("✨ AI로 내용 채우기 — 위 제목·목표만 적고 누르면 문제를 만들어 줘요", expanded=not ws["blocks"]):
        with st.form("t_form", border=False):
            c1, c2 = st.columns(2)
            difficulty = c1.segmented_control("난이도", list(llm.DIFFICULTY), default="standard", key="t_diff",
                                              format_func=lambda k: llm.DIFFICULTY[k][0]) or "standard"
            n_questions = c2.segmented_control("문항 수", [5, 8, 10, 15], default=8, key="t_n") or 8
            extra = st.text_input("더 바라는 점 (선택)", key="t_extra", placeholder="예: 모둠 활동 하나를 넣어 주세요")
            replace = bool(ws["blocks"]) and st.checkbox(
                f"지금 있는 내용 {len(ws['blocks'])}칸을 지우고 새로 만들기", key="t_replace", help="끄면 뒤에 이어서 붙입니다.")
            go_fill = st.form_submit_button("✨ AI로 내용 채우기", type="primary", width="stretch")
        if go_fill:
            try:
                with ai_status("학습지 내용을 만드는 중"):
                    result = llm.generate_from_outline(
                        api_key=api_key, title=ws["title"], subject=ws["subject"], unit=ws["unit"], goals=ws["goals"],
                        level_label=LEVELS[level]["label"], difficulty=difficulty, n_questions=n_questions,
                        extra=extra, model=model,
                    )
            except llm.AnalyzeError as e:
                st.error(str(e))
                return
            new = result.worksheet
            # 적어 둔 머리말은 그대로 두고, 비어 있는 칸만 AI 제안으로 채운다.
            merged = Worksheet(
                kind=ws.get("kind", "standard"),
                title=ws["title"] if ws["title"].strip() and ws["title"] != "새 학습지" else new.title,
                subject=ws["subject"] or new.subject, unit=ws["unit"] or new.unit, lesson=ws["lesson"],
                goals=ws["goals"] or new.goals,
                blocks=([] if replace else current_worksheet().blocks) + new.blocks,
            )
            set_worksheet(merged)
            ss.open_id = None
            ss.answers_on = True
            ss.p_msg = ("success", f"내용 {len(new.blocks)}칸을 만들었어요. 오른쪽 학습지를 확인하고 고칠 곳은 '✏️ 내용 고치기'에서 고쳐 주세요.")
            st.rerun()


# =====================================================================
# 학습지 모양: 예시 학습지를 작게 그려 보여 주고 눌러서 고른다
# =====================================================================
THUMB_WS = Worksheet(
    subject="수학", unit="곱셈", lesson="2차시", title="곱셈 탐험대",
    goals=["(두 자리 수)×(한 자리 수)를 계산할 수 있어요."],
    blocks=[
        Block(type="section", title="개념 익히기"),
        Block(type="concept", items=["곱셈: 같은 수를 여러 번 더하는 것", "올림: 10을 넘으면 윗자리로"]),
        Block(type="section", title="문제 풀기"),
        Block(type="short_answer", body="한 상자에 연필이 12자루씩 있어요. 3상자에는 모두 몇 자루일까요?"),
        Block(type="multiple_choice", body="계산 결과가 가장 큰 것은?", items=["21 × 3", "14 × 4", "32 × 2", "18 × 3"]),
    ],
)
THEME_BLURB = {
    "기본": "깔끔한 기본 모양", "교과서형": "명조체·가운데 제목, 차분한 교과서 느낌",
    "활동지형": "검은 띠 소제목, 회색 카드 문제", "시험지형": "쪽 테두리·칸 나뉜 머리말, 촘촘하게",
    "저학년 놀이형": "둥근 이름표·큰 글씨·아이콘", "노트형": "공책 여백선과 번호 소제목",
}


@st.cache_data(show_spinner=False)
def _thumb(theme_json: str) -> str:
    return render(THUMB_WS, level="elementary", theme=Theme.model_validate_json(theme_json), thumbnail=True)


@st.cache_data(show_spinner=False)
def _kind_thumb(kind: str, theme_json: str) -> str:
    ws = Worksheet.model_validate({**kinds.SAMPLES[kind], "kind": kind})
    return render(ws, level="elementary", theme=Theme.model_validate_json(theme_json), thumbnail=True)


def _pick_kind(kind: str, apply_now: bool) -> None:
    ss.wkind = kind
    if apply_now:  # 완성 화면: 지금 학습지의 배치도 바로 바꾼다
        ss.ws["kind"] = kind


def kind_gallery(cols_n: int = 3, height: int = 300, apply_now: bool = False) -> None:
    """학습지 유형 예시를 보여 주고 누르면 고른다. 유형마다 짜임새와 배치가 다르다."""
    cur = ss.ws.get("kind", "standard") if apply_now else ss.get("wkind", "standard")
    theme_json = current_theme().model_dump_json()
    cols = st.columns(cols_n)
    for i, (k, (icon, name, desc)) in enumerate(kinds.KINDS.items()):
        with cols[i % cols_n]:
            with st.container(border=True):
                st.iframe(_kind_thumb(k, theme_json), height=height)
                st.caption(desc)
                st.button(("✅ " if k == cur else "") + f"{icon} {name}", key=f"kind_pick_{k}", width="stretch",
                          type="primary" if k == cur else "secondary", on_click=_pick_kind, args=(k, apply_now))


def _pick_theme(name: str) -> None:
    for t in list_themes():
        if t.name == name:
            set_theme(t)


# =====================================================================
# 내 학습지 양식: 올린 양식 그림 위의 칸에 문제를 넣는다
# =====================================================================
def _region_change(pi: int, ri: int, field: str, key: str) -> None:
    form = current_form()
    if not form:
        return
    r = form.pages[pi].regions[ri]
    value = ss[key]
    if field == "kind":
        r.kind = value
    elif field == "erase":
        r.erase = bool(value)
    else:  # 위·왼쪽·아래·오른쪽 (%): box는 [ymin, xmin, ymax, xmax] 0~1000
        box = list(r.box)
        box[{"top": 0, "left": 1, "bottom": 2, "right": 3}[field]] = round(float(value) * 10)
        r.box = forms.Region._clean_box(box)
    ss.form = form.model_dump()  # 입력칸 번호가 그대로라 form_rev는 올리지 않는다


def _region_add(pi: int) -> None:
    form = current_form()
    if form:
        form.pages[pi].regions.append(forms.Region())
        set_form(form)


def _region_delete(pi: int, ri: int) -> None:
    form = current_form()
    if form:
        form.pages[pi].regions.pop(ri)
        set_form(form)


def _region_editor(form: forms.Form) -> None:
    rev = ss.get("form_rev", 0)
    st.caption("빨간 칸에 문제가, 파란 칸에 제목이 들어가요. 칸이 어긋나면 아래 숫자를 고치세요 — "
               "종이 맨 위(왼쪽)에서 몇 % 떨어졌는지예요.")
    for pi, page in enumerate(form.pages):
        if len(form.pages) > 1:
            st.markdown(f"**{pi + 1}쪽**" + (" — 내용이 많으면 이 쪽 양식이 반복돼요" if pi == len(form.pages) - 1 else ""))
        st.image(forms.preview(page), width=320)
        for ri, r in enumerate(page.regions):
            k = f"fr{rev}_{pi}_{ri}"
            with st.container(border=True):
                c1, c2, c3 = st.columns([1.4, 1.4, 0.8], vertical_alignment="bottom")
                region_kinds = list(forms.REGION_KINDS)
                c1.selectbox(f"{ri + 1}번 칸", region_kinds, index=region_kinds.index(r.kind), format_func=forms.REGION_KINDS.get,
                             key=f"{k}_kind", on_change=_region_change, args=(pi, ri, "kind", f"{k}_kind"))
                c2.checkbox("원래 글 지우기", value=r.erase, key=f"{k}_erase",
                            help="양식에 예전 문제·제목이 인쇄돼 있으면 켜세요. 그 칸을 흰색으로 덮고 새 내용을 넣어요.",
                            on_change=_region_change, args=(pi, ri, "erase", f"{k}_erase"))
                c3.button("빼기", key=f"{k}_del", on_click=_region_delete, args=(pi, ri), width="stretch")
                cols = st.columns(4)
                for col, (field, label, idx) in zip(cols, (("top", "위 %", 0), ("bottom", "아래 %", 2),
                                                           ("left", "왼쪽 %", 1), ("right", "오른쪽 %", 3))):
                    col.number_input(label, 0.0, 100.0, r.box[idx] / 10, step=0.5, format="%.1f", key=f"{k}_{field}",
                                     on_change=_region_change, args=(pi, ri, field, f"{k}_{field}"))
        st.button("＋ 문제 칸 더하기", key=f"fr{rev}_{pi}_add", on_click=_region_add, args=(pi,))


def _use_saved_form(name: str) -> None:
    try:
        set_form(forms.load_form(name))
    except forms.FormError as e:
        ss.form_msg = ("error", str(e))


def form_panel() -> None:
    """📄 내 학습지 양식: 마음에 드는 학습지(PDF·사진)를 올리면 그 모양 그대로, 정해진 칸 안에 문제를 넣는다."""
    if "form_msg" in ss:
        kind, msg = ss.pop("form_msg")
        (st.success if kind == "success" else st.error)(msg)
    form = current_form()
    if form:
        st.success(f"📄 **{md(form.name)}** 양식에 넣고 있어요 ({len(form.pages)}쪽 · 문제 칸 {form.n_regions()}개)")
        c1, c2 = st.columns(2)
        c1.button("양식 끄기 (기본 모양으로)", on_click=set_form, args=(None,), width="stretch", key="form_off")
        if not LOCAL:  # 인터넷판: 서버에 두지 않고 내 컴퓨터로 내려받는다
            safe_name = re.sub(r'[\\/:*?"<>|]+', "_", form.name).strip() or "내 양식"
            c2.download_button("💾 이 양식 내 컴퓨터에 저장", data=form.model_dump_json(), width="stretch",
                               file_name=f"{safe_name}.hanjang-form.json",
                               mime="application/json", key="form_download",
                               help="다음에 '저장해 둔 양식 파일 열기'로 다시 쓸 수 있어요.")
        else:
            _form_save_popover(c2, form)
        with st.expander("✏️ 칸 위치 확인·고치기", expanded=False):
            _region_editor(form)
    if not LOCAL:
        st.file_uploader("📂 저장해 둔 양식 파일 열기 (.hanjang-form.json)", type=["json"], key="form_file",
                         on_change=_open_form_file)
        saved = []
    else:
        saved = forms.list_forms()
    if saved:
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        pick = c1.selectbox("저장해 둔 양식", saved, key="form_pick")
        c2.button("이 양식 쓰기", on_click=_use_saved_form, args=(pick,), width="stretch", key="form_pick_go")
    _form_new_expander(form, saved)


def _open_form_file() -> None:
    up = ss.get("form_file")
    if not up:
        return
    try:
        set_form(forms.Form.model_validate_json(up.getvalue()))
        ss.form_msg = ("success", "저장해 둔 양식을 열었어요.")
    except ValueError:
        ss.form_msg = ("error", "한장에서 저장한 양식 파일(.hanjang-form.json)이 아니에요.")


def _form_save_popover(col, form: forms.Form) -> None:
    with col.popover("💾 이 양식 저장해 두기", width="stretch"):
        with st.form("form_save", border=False):
            name = st.text_input("양식 이름", value=form.name, key="form_save_name")
            if st.form_submit_button("저장", width="stretch"):
                form.name = name.strip() or form.name
                forms.save_form(form)
                set_form(form)
                ss.form_msg = ("success", f"'{form.name}' 양식을 저장했어요. 다음부터 '저장해 둔 양식'에서 고를 수 있어요.")
                st.rerun()


def _form_new_expander(form: forms.Form | None, saved: list[str]) -> None:
    with st.expander("➕ 새 양식 올리기", expanded=not form and not saved):
        st.caption("머리말·테두리가 있는 **빈 양식**이 가장 깔끔해요. 문제가 이미 적힌 학습지를 올리면 "
                   "그 문제 자리를 흰색으로 지우고 새 문제를 넣어요. PDF는 앞 4쪽까지 쓰고, 내용이 넘치면 마지막 쪽이 반복돼요.")
        up = st.file_uploader("양식 파일 (PDF·사진·캡처)", type=["pdf", "png", "jpg", "jpeg", "webp"], key="form_up")
        with st.form("form_new", border=False):
            name = st.text_input("양식 이름", placeholder="예: 우리 반 수학 익힘 양식", key="form_new_name")
            go_form = st.form_submit_button("✨ 양식 살펴보기 (10초쯤)", type="primary", width="stretch")
        if go_form and not up:
            st.warning("양식 파일을 먼저 올려 주세요.")
        if go_form and up:
            try:
                pages = forms.pages_from_upload(up.getvalue(), up.type or "")
                with ai_status("양식에서 문제를 넣을 칸을 찾는 중"):
                    found = llm.find_form_regions(api_key=api_key, pages=[forms.page_bytes(p) for p in pages], model=model)
            except (forms.FormError, llm.AnalyzeError) as e:
                st.error(str(e))
            else:
                for page, regions in zip(pages, found):
                    page.regions = [forms.Region(**r) for r in regions]
                    if not any(r.kind == "content" for r in page.regions):  # 못 찾았으면 본문 전체를 한 칸으로
                        page.regions.append(forms.Region())
                    forms.snap(page)
                default_name = re.sub(r"\.[A-Za-z0-9]+$", "", up.name) or "내 양식"
                set_form(forms.Form(name=name.strip() or default_name, pages=pages))
                ss.form_msg = ("success", "양식을 살펴봤어요. 오른쪽 미리보기를 확인하고, 칸이 어긋나면 '✏️ 칸 위치 확인·고치기'에서 고치세요.")
                st.rerun()


def theme_gallery(cols_n: int = 3, height: int = 330) -> None:
    """모양 예시를 보여 주고 누르면 바로 바뀐다 (지금 학습지에도 적용)."""
    saved = list_themes()
    cur = current_theme().name
    cols = st.columns(cols_n)
    for i, t in enumerate(saved):
        with cols[i % cols_n]:
            with st.container(border=True):
                st.iframe(_thumb(t.model_dump_json()), height=height)
                st.caption(THEME_BLURB.get(t.name, "저장해 둔 모양"))
                st.button(("✅ " if t.name == cur else "") + t.name, key=f"th_pick_{i}", width="stretch",
                          type="primary" if t.name == cur else "secondary", on_click=_pick_theme, args=(t.name,))


def style_editor() -> None:
    """모양 세부 조정·저장 (고급)."""
    t = ss.theme
    r = ss.theme_rev
    cols = st.columns(2)
    for n, (key, (label, choices)) in enumerate(THEME_OPTIONS.items()):
        opts = list(choices)
        t[key] = cols[n % 2].selectbox(label, opts, index=opts.index(t[key]), format_func=choices.get, key=f"th_{key}_{r}")
    if not LOCAL:  # 인터넷판은 서버에 모양을 저장하지 않는다 (다른 사람에게 보이지 않게)
        st.caption("💡 고친 모양은 지금 학습지에 바로 적용돼요. 학습지를 💾 저장하면 모양도 함께 저장되고, "
                   "그 파일을 다시 열면 모양도 그대로 돌아와요.")
        return
    c1, c2 = st.columns([3, 1])
    t["name"] = c1.text_input("이 모양의 이름", value=t["name"], key=f"th_name_{r}")
    if c2.button("모양 저장", type="primary", width="stretch"):
        if t["name"].strip() in ("", "기본"):
            st.warning("'기본'이 아닌 이름을 붙여 주세요.")
        else:
            save_theme(current_theme())
            _thumb.clear()
            st.success(f"'{t['name']}' 모양을 저장했어요. 다음부터 모양 고르기에 나와요.")


def json_panel() -> None:
    st.caption("학습지 구조를 직접 고치거나, 저장해 둔 JSON을 붙여넣고 '적용'을 누르세요. (익숙한 분만)")
    # 그림 데이터는 매우 길어서 '@그림1' 같은 표시로 바꿔 보여주고, 적용할 때 되돌린다.
    data = current_worksheet().model_dump()
    pictures: list[str] = []
    for b in data["blocks"]:
        if b.get("image"):
            pictures.append(b["image"])
            b["image"] = f"@그림{len(pictures)}"
    current = json.dumps(data, ensure_ascii=False, indent=2)
    edited = st.text_area("학습지 구조 (JSON)", value=current, height=420, key=f"json_{hash(current)}")
    if st.button("적용", type="primary"):
        try:
            obj = json.loads(edited)
            for b in obj.get("blocks", []) if isinstance(obj, dict) else []:
                m = re.fullmatch(r"@그림(\d+)", str(b.get("image", "")))
                if m and 0 < int(m.group(1)) <= len(pictures):
                    b["image"] = pictures[int(m.group(1)) - 1]
            set_worksheet(Worksheet.model_validate(obj))
            st.rerun()
        except json.JSONDecodeError as e:
            st.error(f"JSON 형식 오류: {e.lineno}번째 줄 {e.colno}번째 글자 근처를 확인하세요. ({e.msg})")
        except ValidationError as e:
            st.error(f"구조 오류:\n{e}")


# =====================================================================
# 배울 내용 고르기 (영역을 바꿔도 고른 것이 남는다: ss.sel["<prefix>|<과목>"] = 코드 목록)
# =====================================================================
def _picked_codes(prefix: str, subject: str) -> list[str]:
    return ss.setdefault("sel", {}).setdefault(f"{prefix}|{subject}", [])


def _toggle_code(prefix: str, subject: str, code: str) -> None:
    codes = _picked_codes(prefix, subject)
    if ss.get(f"{prefix}_std_{code}"):
        if code not in codes:
            codes.append(code)
    elif code in codes:
        codes.remove(code)


def _unpick(prefix: str, subject: str, code: str | None = None) -> None:
    codes = _picked_codes(prefix, subject)
    for c in ([code] if code else list(codes)):
        if c in codes:
            codes.remove(c)
        ss.pop(f"{prefix}_std_{c}", None)


def _picked(prefix: str, subject: str, grade: int) -> list:
    """지금 학년군에 맞는 고른 성취기준 (다른 학년군에서 고른 것은 빼고)."""
    top = curriculum.band_of(grade).split("-")[1]
    return curriculum.find([c for c in _picked_codes(prefix, subject) if c.startswith(top)])


def standard_picker(grade: int, subject: str, prefix: str) -> None:
    """영역 버튼을 누르고, 배울 내용을 체크한다. 내용마다 쉬운 설명과 배우는 시기를 함께 보여 준다."""
    show_all = st.toggle(
        f"{curriculum.band_label(grade)} 내용 모두 보기", key=f"{prefix}_all_{grade}_{subject}",
        help="교과서·학교마다 배우는 학년이 조금 다를 수 있어요. 아이 교과서에 있는데 목록에 없으면 켜 보세요.",
    )
    codes = _picked_codes(prefix, subject)
    area_names = curriculum.areas(grade, subject, this_grade_only=not show_all)
    counts = {a: sum(s.code in codes for s in curriculum.standards(grade, subject, a, this_grade_only=not show_all))
              for a in area_names}
    area = st.pills(
        "영역 — 눌러서 바꿔 보세요 (여러 영역에서 골라도 모두 남아요)", area_names, required=True,
        default=area_names[0], key=f"{prefix}_area_{grade}_{subject}_{show_all}",
        format_func=lambda a: f"{a} ✔{counts[a]}" if counts[a] else a,
    ) or area_names[0]
    topic = None
    for std in curriculum.standards(grade, subject, area, this_grade_only=not show_all):
        if std.topic and std.topic != topic:
            topic = std.topic
            st.markdown(f"**📍 {topic}**")
        with st.container(border=True):
            st.checkbox(f"**{md(std.easy or std.text)}**", value=std.code in codes, key=f"{prefix}_std_{std.code}",
                        on_change=_toggle_code, args=(prefix, subject, std.code))
            st.caption(md(f"교육과정: {std.text}" + (f"  \n🗓 {std.when}" if std.when else "")))


def basket(entries: list[tuple[str, str, int]], title: str = "🧺 고른 배울 내용") -> int:
    """고른 내용을 한쪽에 늘 보여 주고 ✕로 뺄 수 있게 한다. 고른 개수를 돌려준다."""
    total = sum(len(_picked(p, s, g)) for p, s, g in entries)
    with st.container(border=True):
        st.markdown(f"#### {title} ({total})")
        if not total:
            st.caption("아직 없어요. 왼쪽에서 체크하면 여기에 모여요.")
        for prefix, subject, grade in entries:
            picked = _picked(prefix, subject, grade)
            if picked and len(entries) > 1:
                st.markdown(f"**{subject}**")
            for std in picked:
                c1, c2 = st.columns([3, 1.2], vertical_alignment="center")
                c1.caption(md(f"**{std.area}** · {std.easy or std.text}"))
                c2.button("빼기", key=f"rm_{prefix}_{std.code}", on_click=_unpick, args=(prefix, subject, std.code))
        if total:
            st.button("모두 지우기", key=f"rm_all_{entries[0][0]}", width="stretch",
                      on_click=lambda: [_unpick(p, s) for p, s, _ in entries])
    return total


# =====================================================================
# 만들기 공통: 꾸미기 설정, AI 실행, 결과로 넘어가기
# =====================================================================
def settings_form(prefix: str, button_label: str, n_picked: int = 0, single: bool = True) -> dict | None:
    """① 모양(누르면 바로 바뀜) → ② 설정과 만들기 버튼.
    설정 칸은 폼으로 묶여 있어서 글을 쓰고 엔터 없이 바로 만들기를 눌러도 그대로 적용된다. 누르면 설정을 돌려준다."""
    st.markdown("##### 📋 ① 학습지 유형 — 어떤 짜임새로 만들까요?")
    kind_gallery()
    st.markdown("##### 🎨 ② 학습지 모양 — 눌러서 고르세요")
    with st.expander("📄 내 학습지 양식에 넣기 (선택)" + (" — 사용 중" if ss.get("form") else ""), expanded=bool(ss.get("form"))):
        form_panel()
    if ss.get("form"):
        st.caption("양식을 쓰는 동안 아래 모양은 문제 번호·소제목·글꼴 모양에만 쓰여요.")
    theme_gallery()
    st.markdown("##### ⚙️ ③ 만들기 설정")
    with st.form(f"{prefix}_form"):
        task_type, theme_text = "project", ""
        if not single:
            task_type = st.segmented_control(
                "과제 형태", list(llm.TASK_TYPES), default=ss.get("nl", {}).get("task", "project"), key=f"{prefix}_task",
                format_func=lambda k: llm.TASK_TYPES[k][0],
            ) or "project"
            st.caption("  \n".join(f"**{name}** — {desc}" for name, desc in llm.TASK_TYPES.values()))
            theme_text = st.text_input("주제·상황 (선택)", value=ss.get("nl", {}).get("theme", ""), key=f"{prefix}_theme_text",
                                       placeholder="예: 우리 동네 시장 조사, 가족 캠핑 계획 세우기, 우리 반 축구 대회")
        c1, c2 = st.columns(2)
        with c1:
            difficulty = st.segmented_control(
                "난이도", list(llm.DIFFICULTY), default="standard", key=f"{prefix}_diff",
                format_func=lambda k: llm.DIFFICULTY[k][0], help="  \n".join(f"{v[0]}: {v[1]}" for v in llm.DIFFICULTY.values()),
            ) or "standard"
        with c2:
            n_questions = st.segmented_control("몇 문제?", [5, 8, 10, 15], default=8, key=f"{prefix}_n") or 8
        answers = st.toggle("정답·해설 쪽도 함께 만들기 (학습지 뒤에 새 쪽으로)", value=ss.answers_on, key=f"{prefix}_ans")
        review = st.toggle("🔍 다 만든 뒤 AI가 문항을 다시 풀어 보며 정답·학년 범위 검토 (20~40초 더 걸려요)",
                           value=ss.get("review_on", True), key=f"{prefix}_review",
                           help="계산 문제의 정답은 검토를 꺼도 프로그램이 늘 다시 계산해서 바로잡아요. "
                                "검토를 켜면 문장으로 된 문제, 학년에 맞지 않는 내용, 답이 애매한 문제까지 AI가 한 번 더 살펴 고쳐요.")
        mix = "separate"
        if single and n_picked > 1:
            mix = st.segmented_control(
                f"고른 내용 {n_picked}개로 어떻게 만들까요?", list(llm.MIX_LABELS), default="separate", key=f"{prefix}_mix",
                format_func=llm.MIX_LABELS.get,
                help="영역별로 차례대로: 내용마다 소제목을 나눠 차례로 풀어요. · 복합 문제: 여러 내용을 함께 써야 풀리는 문제로 만들어요.",
            ) or "separate"
        unit = st.text_input("아이 교과서 단원명 (선택)", placeholder="예: 3. 나눗셈 — 적으면 단원 흐름에 맞춰 줘요",
                             key=f"{prefix}_unit") if single else ""
        extra = st.text_input("더 바라는 점 (선택)", placeholder="예: 공룡을 좋아하는 아이예요. 문제에 공룡이 나오게 해 주세요.",
                              value=ss.get("nl", {}).get("extra", ""), key=f"{prefix}_extra")
        st.caption("🖼 수학 그림(묶음·수직선·분수·시계·그래프)과 🧸 그림 카드(아이콘 그림)는 필요한 곳에 자동으로 들어가요. "
                   "삽화가 더 필요하면 완성한 뒤 '그림' 칸을 넣고 🔎 그림 찾기나 🎨 AI 그리기를 쓰세요.")
        st.write("")
        submitted = st.form_submit_button(button_label, type="primary", width="stretch")
    if not submitted:
        return None
    ss.answers_on = answers
    ss.review_on = review
    return {"wkind": ss.get("wkind", "standard"), "review": review,
            "difficulty": difficulty, "n_questions": n_questions, "mix": mix, "unit": unit, "extra": extra,
            "task_type": task_type, "theme_text": theme_text}


def _run(req: dict) -> None:
    """학습지를 만들고 완성 화면으로 넘어간다. req는 다시 만들기에 그대로 쓰도록 저장한다."""
    kind = req["kind"]
    try:
        with ai_status("학습지를 만드는 중" if kind == "single" else "융합 과제를 만드는 중"):
            if kind == "single":
                result = llm.generate_for_standards(
                    api_key=api_key, grade=req["grade"], subject=req["subject"], standards=curriculum.find(req["codes"]),
                    unit_name=req["unit"], difficulty=req["difficulty"], n_questions=req["n_questions"],
                    extra=req["extra"], model=model, mix=req["mix"], kind=req.get("wkind", "standard"),
                )
            else:
                result = llm.generate_integrated(
                    api_key=api_key, grade=req["grade"],
                    picks={s: curriculum.find(c) for s, c in req["picks"].items()},
                    candidates={s: curriculum.standards(req["grade"], s) for s in req["picks"]},
                    task_type=req["task_type"], theme=req["theme_text"], difficulty=req["difficulty"],
                    n_questions=req["n_questions"], extra=req["extra"], model=model, kind=req.get("wkind", "standard"),
                )
    except llm.AnalyzeError as e:
        st.error(str(e))
        return
    ws = result.worksheet
    fixes = list(llm.LAST_FIXES.get())  # 계산 문제: 프로그램이 다시 계산해 고친 정답
    if req.get("review", True):
        if kind == "single":
            subject, limits = req["subject"], curriculum.limits(req["grade"], req["subject"], curriculum.find(req["codes"]))
        else:
            subject = " · ".join(req["picks"])
            limits = "\n".join(curriculum.limits(req["grade"], s, curriculum.find(c)) for s, c in req["picks"].items())
        try:
            with ai_status("다 만든 학습지를 검토하는 중"):
                ws, notes = llm.review_worksheet(api_key=api_key, ws=ws, grade=req["grade"], subject=subject,
                                                 limits=limits, model=model)
            fixes += notes
        except llm.AnalyzeError as e:
            fixes.append(f"AI 검토를 마치지 못했어요 — 문제와 정답을 한 번 더 확인해 주세요. ({str(e).splitlines()[0]})")
    set_worksheet(ws)
    ss.review_notes = (bool(req.get("review", True)), fixes)
    ss.doc_level = "lower" if req["grade"] <= 2 else "elementary"
    ss.last_req = req
    ss.pop("nl_msg", None)
    ss.open_id = None
    ss.p_msg = ("success", f"'{ws.title}' 학습지를 만들었어요!")
    ss.page = "result"
    st.rerun()


def nav(prev: str | None, next_page: str | None = None, next_label: str = "다음 →", disabled: bool = False,
        hint: str = "", stacked: bool = False) -> None:
    st.write("")
    if stacked:  # 좁은 칸: 다음 버튼을 크게 위에, 이전 버튼을 아래에
        if next_page:
            st.button(next_label, type="primary", width="stretch", on_click=go, args=(next_page,), disabled=disabled,
                      key=f"nav_next_{ss.page}")
            if disabled and hint:
                st.caption(hint)
        if prev:
            st.button("← 이전", width="stretch", on_click=go, args=(prev,), key=f"nav_prev_{ss.page}")
        return
    c1, _, c3 = st.columns([1, 2, 1.4])
    if prev:
        c1.button("← 이전", width="stretch", on_click=go, args=(prev,), key=f"nav_prev_{ss.page}")
    if next_page:
        c3.button(next_label, type="primary", width="stretch", on_click=go, args=(next_page,), disabled=disabled,
                  key=f"nav_next_{ss.page}")
        if disabled and hint:
            c3.caption(hint)


# =====================================================================
# 🏠 처음 화면
# =====================================================================
_SUBJECT_OF_CODE = {"국": "국어", "수": "수학", "영": "영어", "사": "사회", "과": "과학"}  # 4수01-03 → 수학


def _start(page: str) -> None:
    """처음 화면 카드로 새로 시작: 앞에서 말로 주문한 내용은 지운다."""
    ss.pop("nl", None)
    ss.pop("nl_msg", None)
    go(page)


def _apply_request(text: str) -> None:
    """말로 쓴 요청 → 학년·과목·배울 내용을 골라 두고 '배울 내용' 화면으로 보낸다. 만들기 전에 확인할 수 있게."""
    d = llm.understand_request(api_key=api_key, text=text, catalog=curriculum.catalog(), default_grade=ss.grade, model=model)
    grade = d.get("grade") if isinstance(d.get("grade"), int) and 1 <= d["grade"] <= 6 else 0
    told_grade = bool(re.search(r"[1-6]\s*학년|[일이삼사오육]\s*학년|초\s*[1-6]", text))
    guessed = grade and not told_grade
    grade = grade or ss.grade
    allowed = curriculum.subjects_for(grade)
    subjects = [x for x in dict.fromkeys(d.get("subjects") or []) if x in allowed]
    top = curriculum.band_of(grade).split("-")[1]
    codes: dict[str, list[str]] = {}
    for std in curriculum.find([c for c in d.get("codes") or [] if isinstance(c, str)]):
        subj = _SUBJECT_OF_CODE.get(std.code[1:2], "")
        if subj in allowed and std.code.startswith(top):  # 그 학년군의 성취기준만
            codes.setdefault(subj, []).append(std.code)
            if subj not in subjects:
                subjects.append(subj)
    if not subjects:
        raise llm.AnalyzeError("어떤 과목을 원하시는지 알아듣지 못했어요. 예: '3학년 수학 덧셈과 뺄셈'처럼 과목이나 배울 내용을 적어 주세요.")
    note = "" if told_grade else (f" (학년을 말하지 않으셔서 내용에 맞춰 {grade}학년으로 골랐어요)" if guessed
                                  else f" (학년을 말하지 않으셔서 {grade}학년으로 골랐어요)")
    if len(subjects) > 3:
        note += f" 과목은 3개까지라 {', '.join(subjects[3:])}은(는) 뺐어요."
        subjects = subjects[:3]
    for key in ("w_grade", f"w_subject_{grade}", f"w_fsubjects_{grade}"):
        ss.pop(key, None)  # 학년·과목 버튼이 새 값으로 다시 그려지게
    ss.grade = grade
    extra = str(d.get("extra") or "").strip()
    wkind = d.get("kind") if d.get("kind") in kinds.KINDS else "standard"
    ss.wkind = wkind
    if len(subjects) == 1:
        subj = subjects[0]
        ss.subject = subj
        _unpick("p", subj)
        _picked_codes("p", subj).extend(codes.get(subj, []))
        ss.nl = {"extra": extra, "kind": wkind}
        ss.page = "s2"
    else:
        ss.fusion_subjects = subjects
        for subj in subjects:
            _unpick(f"f_{subj}", subj)
            _picked_codes(f"f_{subj}", subj).extend(codes.get(subj, []))
        task = d.get("task_type") if d.get("task_type") in llm.TASK_TYPES else "separate"
        # 엮는 과제면 남은 말은 주제로, 나눠 만들 때는 바라는 점으로
        ss.nl = {"task": task, "theme": extra if task != "separate" else "", "extra": extra if task == "separate" else "",
                 "kind": wkind}
        ss.page = "f2"
    n = sum(len(v) for v in codes.values())
    ss.nl_msg = (f"💬 요청을 보고 **{grade}학년 {' · '.join(subjects)}**에서 배울 내용 {n}개를 골라 두었어요{note}. "
                 + (f"학습지 유형은 **{kinds.label(wkind)}**로 골라 두었어요. " if wkind != "standard" else "")
                 + "맞는지 확인하고, 고칠 것이 있으면 고친 뒤 **다음**을 누르세요."
                 + (" 과목이 여럿이라 한 장에 함께 담는 '여러 과목' 학습지로 만들어요." if len(subjects) > 1 else ""))


def _show_nl_msg() -> None:
    if ss.get("nl_msg"):
        st.info(md(ss.nl_msg))


def page_home() -> None:
    st.markdown("# 📝 한장")
    st.markdown("#### 클릭 몇 번이면, 교육과정에 맞는 **A4 학습지와 정답지**가 완성돼요.")
    if not api_key:
        with st.container(border=True):
            st.markdown("### 🔑 먼저 Gemini API 키를 넣어 주세요")
            st.caption("학습지는 구글 Gemini AI로 만들어요. 키는 무료로 받을 수 있고, 한 번만 하면 돼요. "
                       "넣은 키는 왼쪽 ⚙️ 설정 칸에 들어가요.")
            key_help()
    with st.container(border=True):
        st.markdown("### 💬 말로 주문하기")
        with st.form("nl_form", border=False):
            text = st.text_input("어떤 학습지가 필요한지 편하게 적어 주세요", key="nl_text",
                                 placeholder="예: 3학년 수학 덧셈과 뺄셈이랑 국어 발표 관련 내용으로 만들어 줘")
            asked = st.form_submit_button("✨ 알아서 골라 주기", type="primary", width="stretch")
        st.caption("학년·과목·배울 내용을 골라 둔 화면으로 넘어가요. 확인만 하고 만들면 돼요. (5초쯤)")
        if asked:
            try:
                with ai_status("요청을 읽고 배울 내용을 고르는 중"):
                    _apply_request(text)
            except llm.AnalyzeError as e:
                st.error(str(e))
            else:
                st.rerun()
    st.markdown("##### 또는 직접 골라서 만들기")
    cards = [
        ("📘", "한 과목 학습지", "학년·과목·배울 내용만 고르면\n학습지와 정답지를 만들어요.", "s1", "가장 쉬워요 · 처음이라면 여기!"),
        ("🔗", "여러 과목 융합 과제", "수학+사회처럼 두세 과목을 엮어\n생활 속 주제로 푸는 과제를 만들어요.", "f1", "프로젝트·이야기 문제·탐구"),
        ("📂", "내 자료로 만들기", "교과서·학습지 파일, 마음에 드는\n학습지 사진으로 만들거나 직접 만들어요.", "tools", "선생님·익숙한 분께 추천"),
    ]
    cols = st.columns(3, gap="medium")
    for col, (emoji, title, desc, page, tag) in zip(cols, cards):
        with col.container(border=True):
            st.html(f'<div class="hj-card-emoji">{emoji}</div>')
            st.markdown(f"### {title}")
            st.markdown(desc.replace("\n", "  \n"))
            st.caption(tag)
            st.button("시작하기 →", key=f"home_{page}", width="stretch", on_click=_start, args=(page,))
    st.write("")
    c1, c2 = st.columns(2, gap="medium")
    with c1.container(border=True):
        st.markdown("**📂 저장해 둔 학습지 다시 열기**")
        st.file_uploader("한장에서 저장한 HTML 파일을 올리면 이어서 고칠 수 있어요", type=["html", "htm", "json"],
                         key="home_open", on_change=_open_saved, args=("home_open",))
        if "open_msg" in ss:
            kind, msg = ss.pop("open_msg")
            (st.success if kind == "success" else st.error)(msg)
    with c2.container(border=True):
        st.markdown("**처음이세요? 이렇게 하면 돼요**")
        st.markdown("1. 위에서 **📘 한 과목 학습지**를 누르세요  \n2. 학년·과목 → 배울 내용 → 모양을 차례로 고르세요  \n"
                    "3. **✨ 학습지 만들기**를 누르고 20~30초 기다리면 끝!  \n4. **💾 저장**하고 **🖨 인쇄**하세요")
        if ss.ws["blocks"]:
            st.button(f"✏️ 만들던 학습지 계속하기 — '{ss.ws['title']}'", width="stretch", on_click=go, args=("result",))


# =====================================================================
# 📘 한 과목 학습지 (s1 → s2 → s3)
# =====================================================================
SINGLE_STEPS = ["학년·과목", "배울 내용", "꾸미기", "완성"]


def _grade_subject(multi: bool) -> None:
    st.pills("학년", [1, 2, 3, 4, 5, 6], default=ss.grade, required=True, key="w_grade",
             format_func=lambda g: f"{g}학년", on_change=_sync, args=("grade", "w_grade"))
    subjects = curriculum.subjects_for(ss.grade)
    if multi:
        cur = [s for s in ss.fusion_subjects if s in subjects] or subjects[:2]
        wkey = f"w_fsubjects_{ss.grade}"  # 학년마다 고를 수 있는 과목이 달라서 칸을 따로 둔다
        ss.fusion_subjects = st.pills("엮을 과목 — 2~3개를 눌러 고르세요", subjects, selection_mode="multi", default=cur,
                                      key=wkey) or []
    else:
        if ss.subject not in subjects:
            ss.subject = subjects[0]
        ss.subject = st.pills("과목", subjects, default=ss.subject, required=True, key=f"w_subject_{ss.grade}") or subjects[0]
    if ss.grade <= 2:
        st.caption("영어·사회·과학은 3학년부터 배워요.")


def page_single(step: int) -> None:
    step_bar(SINGLE_STEPS, step)
    if step == 1:
        st.markdown("## 몇 학년, 어떤 과목인가요?")
        _grade_subject(multi=False)
        nav("home", "s2", "다음: 배울 내용 고르기 →")
    elif step == 2:
        st.markdown(f"## {ss.grade}학년 {ss.subject} — 무엇을 공부할까요?")
        _show_nl_msg()
        st.caption("하나만 골라도 되고, 약한 부분만 여러 개 골라도 돼요. 영역을 바꿔도 고른 것은 그대로 남아요.")
        left, right = st.columns([2.3, 1], gap="large")
        with left:
            standard_picker(ss.grade, ss.subject, "p")
        with right:
            n = basket([("p", ss.subject, ss.grade)])
            nav("s1", "s3", "다음: 꾸미기 →", disabled=not n, hint="배울 내용을 하나 이상 골라 주세요", stacked=True)
    else:
        picked = _picked("p", ss.subject, ss.grade)
        if not picked:
            st.warning("배울 내용을 먼저 골라 주세요.")
            nav("s2")
            return
        st.markdown("## 마지막으로, 어떻게 만들까요?")
        st.caption(md("고른 내용: " + " · ".join(s.easy or s.text for s in picked)))
        opts = settings_form("p", f"✨ 학습지 만들기 (배울 내용 {len(picked)}개)", len(picked), single=True)
        if opts:
            _run({"kind": "single", "grade": ss.grade, "subject": ss.subject, "codes": [s.code for s in picked], **opts})
        nav("s2")


# =====================================================================
# 🔗 여러 과목 융합 과제 (f1 → f2 → f3)
# =====================================================================
FUSION_STEPS = ["학년·과목", "배울 내용 (선택)", "과제·꾸미기", "완성"]


def page_fusion(step: int) -> None:
    step_bar(FUSION_STEPS, step)
    subjects = ss.fusion_subjects
    if step == 1:
        st.markdown("## 몇 학년, 어떤 과목들을 엮을까요?")
        _grade_subject(multi=True)
        n = len(ss.fusion_subjects)
        ok = 2 <= n <= 3
        if n > 3:
            st.warning("과목은 3개까지 엮을 수 있어요.")
        nav("home", "f2", "다음 →", disabled=not ok, hint="과목을 2~3개 골라 주세요")
    elif step == 2:
        st.markdown(f"## {ss.grade}학년 {' + '.join(subjects)} — 꼭 넣을 내용이 있나요?")
        _show_nl_msg()
        st.caption("고르지 않은 과목은 주제에 어울리는 내용을 AI가 그 학년 목록에서 골라요. 바로 '다음'을 눌러도 돼요.")
        left, right = st.columns([2.3, 1], gap="large")
        with left:
            tabs = st.tabs([f"{s}" for s in subjects])
            for tab, subj in zip(tabs, subjects):
                with tab:
                    standard_picker(ss.grade, subj, f"f_{subj}")
        with right:
            basket([(f"f_{s}", s, ss.grade) for s in subjects])
            nav("f1", "f3", "다음: 과제·꾸미기 →", stacked=True)
    else:
        st.markdown("## 어떤 과제로 만들까요?")
        opts = settings_form("f", f"✨ 융합 과제 만들기 ({' + '.join(subjects)})", single=False)
        if opts:
            picks = {s: [c.code for c in _picked(f"f_{s}", s, ss.grade)] for s in subjects}
            _run({"kind": "fusion", "grade": ss.grade, "picks": picks, **opts})
        nav("f2")


# =====================================================================
# 📂 내 자료로 만들기
# =====================================================================
TOOLS = {
    "material": ("📄", "파일·글로 만들기", "교과서 본문, 수업 자료(PDF·사진·한글·워드)를 넣으면 학습지로 정리해요."),
    "reference": ("🖼️", "마음에 드는 학습지 따라 만들기", "학습지 사진·PDF를 올리면 내용과 모양을 가져와요."),
    "blank": ("✏️", "빈 학습지에서 직접 만들기", "제목·목표만 적고 AI로 채우거나, 한 칸씩 직접 만들어요."),
    "open": ("📂", "저장한 학습지 열기", "한장에서 저장한 HTML 파일을 다시 열어 고쳐요."),
}


def _model_note(used: str) -> None:
    if used and used != model:
        st.info(f"{model} 서버가 붐벼서 {used} 모델로 대신 만들었어요.")


def page_tools() -> None:
    st.markdown("## 📂 내 자료로 만들기")
    st.segmented_control("누구를 위한 학습지인가요?", list(LEVEL_CHOICES), default=ss.doc_level, key="w_level",
                         format_func=LEVEL_CHOICES.get, on_change=_sync, args=("doc_level", "w_level"))
    st.write("")
    tool = ss.get("tool")
    cols = st.columns(4, gap="small")
    for col, (key, (emoji, title, desc)) in zip(cols, TOOLS.items()):
        with col.container(border=True):
            st.markdown(f"### {emoji}")
            st.markdown(f"**{title}**")
            st.caption(desc)
            if key == "blank":
                st.button("시작 →", key=f"tool_{key}", width="stretch", type="primary", on_click=_start_blank)
            else:
                st.button("선택 ✅" if tool == key else "선택 →", key=f"tool_{key}", width="stretch",
                          type="primary" if tool == key else "secondary", on_click=lambda k=key: ss.update(tool=k))
    st.write("")
    if tool == "material":
        with st.container(border=True):
            tool_material()
    elif tool == "reference":
        with st.container(border=True):
            tool_reference()
    elif tool == "open":
        with st.container(border=True):
            st.file_uploader("한장에서 저장한 HTML 파일", type=["html", "htm", "json"], key="tools_open",
                             on_change=_open_saved, args=("tools_open",))
            if "open_msg" in ss:
                kind, msg = ss.pop("open_msg")
                (st.success if kind == "success" else st.error)(msg)


def tool_material() -> None:
    st.markdown("#### 📄 파일·글로 만들기")
    uploads = st.file_uploader("파일 올리기 (PDF·사진·HWPX·DOCX·TXT, 여러 개 가능)", type=extract.SUPPORTED,
                               accept_multiple_files=True, key="mat_files")
    with st.form("mat_form", border=False):
        pasted = st.text_area("또는 내용 붙여넣기", height=150, key="mat_text", placeholder="수업 자료 내용")
        extra = st.text_input("더 바라는 점 (선택)", key="mat_extra", placeholder="예: 마지막에 서술형 문항 2개를 넣어 줘")
        make_questions = st.checkbox("문제가 부족하면 AI가 더 만들기", value=True, key="mat_q", help="끄면 원문에 있는 문제만 옮겨요.")
        go_mat = st.form_submit_button("✨ 학습지로 만들기", type="primary", width="stretch")
    if go_mat:
        try:
            text, files = extract.prepare_many([(u.name, u.getvalue()) for u in uploads or []])
            with ai_status("자료를 분석하는 중"):
                result = llm.analyze(
                    api_key=api_key, text=f"{text}\n\n{pasted}".strip(), files=files,
                    model=model, level_label=LEVELS[level]["label"], make_questions=make_questions, extra=extra,
                )
        except (extract.ExtractError, llm.AnalyzeError) as e:
            st.error(str(e))
            return
        _model_note(result.model_used)
        set_worksheet(result.worksheet)
        ss.last_req = None
        ss.p_msg = ("success", f"자료를 학습지로 정리했어요 (내용 {len(result.worksheet.blocks)}칸).")
        ss.page = "result"
        st.rerun()


def tool_reference() -> None:
    st.markdown("#### 🖼️ 마음에 드는 학습지 따라 만들기")
    uploads = st.file_uploader("참고할 학습지 사진·PDF", type=extract.VISUAL_TYPES, accept_multiple_files=True, key="ref_files")
    if uploads:
        imgs = [u for u in uploads if not u.name.lower().endswith(".pdf")]
        if imgs:
            st.image([u.getvalue() for u in imgs[:4]], width=140)
    with st.form("ref_form", border=False):
        c1, c2 = st.columns(2)
        want_content = c1.checkbox("내용 가져오기", value=True, key="ref_content", help="글·문제·그림·배치 순서를 옮겨요.")
        want_style = c2.checkbox("모양 가져오기", value=True, key="ref_style", help="머리말·소제목·번호 모양, 글꼴, 테두리 등을 비슷하게 골라요.")
        style_name = st.text_input("가져온 모양의 이름", value="참고 학습지 스타일", key="ref_style_name")
        go_ref = st.form_submit_button("✨ 학습지 분석하기", type="primary", width="stretch")
    if go_ref and not uploads:
        st.warning("참고할 학습지 사진이나 PDF를 먼저 올려 주세요.")
    if go_ref and uploads:
        try:
            _, files = extract.prepare_many([(u.name, u.getvalue()) for u in uploads or []])
            with ai_status("학습지를 분석하는 중"):
                result = llm.analyze(
                    api_key=api_key, files=files, want_content=want_content, want_style=want_style,
                    model=model, level_label=LEVELS[level]["label"], style_name=style_name.strip() or "참고 학습지 스타일",
                )
        except (extract.ExtractError, llm.AnalyzeError) as e:
            st.error(str(e))
            return
        _model_note(result.model_used)
        msgs = []
        if result.worksheet:
            set_worksheet(result.worksheet)
            pics = [b for b in result.worksheet.blocks if b.type == "image"]
            msgs.append(f"내용 {len(result.worksheet.blocks)}칸을 가져왔어요."
                        + (f" 원본에서 그림 {sum(bool(b.image) for b in pics)}개를 잘라 넣었어요." if pics else ""))
        elif not ss.ws["blocks"]:
            _load_sample()  # 빈 학습지에서는 모양이 안 보이므로 예시 내용에 입혀 보여 준다
        if result.theme:
            set_theme(result.theme)
            if LOCAL:
                save_theme(result.theme)
                _thumb.clear()
                msgs.append(f"모양 '{result.theme.name}'을(를) 가져와 저장했어요. 다음에도 모양 고르기에서 쓸 수 있어요.")
            else:
                msgs.append(f"모양 '{result.theme.name}'을(를) 가져와 입혔어요.")
        ss.last_req = None
        ss.p_msg = ("success", " ".join(msgs))
        ss.page = "result"
        st.rerun()


# =====================================================================
# ✅ 완성: 큰 미리보기 + 저장·인쇄 + 고치기·모양 바꾸기
# =====================================================================
def page_result() -> None:
    ws = current_worksheet()
    html = render(ws, level=level, footer=footer, theme=current_theme(), show_answers=show_answers, form=current_form())
    if ss.get("last_req"):
        step_bar(SINGLE_STEPS if ss.last_req["kind"] == "single" else FUSION_STEPS, 4)
    if "p_msg" in ss:
        kind, msg = ss.pop("p_msg")
        (st.success if kind == "success" else st.error)("🎉 " + msg if kind == "success" else msg)
        if kind == "success" and ss.get("last_req"):
            reviewed, fixes = ss.get("review_notes", (False, []))
            done = "AI 검토와 계산 검산을 거쳤지만" if reviewed else "계산 문제는 프로그램이 검산했지만"
            st.info(f"{done}, 아이에게 주기 전에 문제와 정답을 한 번 훑어봐 주세요.")

    reviewed, fixes = ss.get("review_notes", (False, []))
    if fixes:
        with st.expander(f"🔍 검토하며 바로잡은 것 {len(fixes)}개 — 눌러서 보기"):
            st.markdown("\n".join(f"- {md(f)}" for f in fixes))
    filename = re.sub(r'[\\/:*?"<>|\s]+', "_", ws.title).strip("_") or "worksheet"
    a1, a2, a3, a4 = st.columns(4)
    a1.download_button("💾 저장하기", html, file_name=f"{filename}.html", mime="text/html", type="primary", width="stretch",
                       help="이 파일을 크롬·엣지로 열면 인쇄할 수 있고, 한장에서 다시 열어 고칠 수도 있어요.")
    with a2.popover("🖨️ 인쇄하는 법", width="stretch"):
        st.markdown("1. 왼쪽 **💾 저장하기**를 눌러 파일을 저장해요\n2. 저장한 파일을 **더블클릭**(크롬·엣지로 열림)\n"
                    "3. 오른쪽 위 **인쇄하기** 버튼 또는 **Ctrl+P**\n4. 용지 **A4**, '머리글과 바닥글' **끄기** → 인쇄\n\n"
                    "PDF로 저장하려면 3번에서 프린터를 **'PDF로 저장'** 으로 바꾸세요.")
    if ss.get("last_req"):
        if a3.button("🔄 같은 설정으로 다시 만들기", width="stretch"):
            _run(ss.last_req)
    else:
        a3.button("📂 다른 자료로 만들기", width="stretch", on_click=go, args=("tools",))
    a4.button("➕ 새 학습지 만들기", width="stretch", on_click=go, args=("home",))

    left, right = st.columns([1, 1.25], gap="large")
    with left:
        tabs = st.tabs(["✏️ 내용 고치기", "🎨 모양 바꾸기", "📝 제목·목표", "🛠 더 많은 설정"])
        with tabs[0]:
            block_list()
        with tabs[1]:
            with st.expander(f"📋 학습지 유형 — 지금: {kinds.label(ss.ws.get('kind', 'standard'))}"):
                st.caption("누르면 오른쪽 학습지의 배치가 바로 바뀌어요. 문제 구성까지 그 유형에 맞추려면 아래 버튼으로 다시 만드세요.")
                kind_gallery(cols_n=2, height=260, apply_now=True)
                if ss.get("last_req") and st.button(f"🔄 '{kinds.label(ss.ws.get('kind', 'standard'))}'으로 다시 만들기",
                                                    width="stretch", key="kind_remake"):
                    _run({**ss.last_req, "wkind": ss.ws.get("kind", "standard")})
            with st.expander("📄 내 학습지 양식에 넣기" + (" — 사용 중" if ss.get("form") else ""), expanded=bool(ss.get("form"))):
                form_panel()
            st.caption("눌러서 고르면 오른쪽 학습지에 바로 적용돼요."
                       + (" 양식을 쓰는 동안에는 문제 번호·소제목·글꼴 모양에만 쓰여요." if ss.get("form") else ""))
            theme_gallery(cols_n=2, height=300)
        with tabs[2]:
            header_panel()
        with tabs[3]:
            st.toggle("정답·해설 쪽 넣기", value=ss.answers_on, key="w_answers", on_change=_sync, args=("answers_on", "w_answers"))
            st.segmented_control("글자 크기·답 칸 (학교급)", list(LEVEL_CHOICES), default=ss.doc_level, key="w_level_r",
                                 format_func=LEVEL_CHOICES.get, on_change=_sync, args=("doc_level", "w_level_r"))
            with st.expander("모양 세부 조정·저장"):
                style_editor()
            with st.expander("JSON으로 직접 고치기 (익숙한 분만)"):
                json_panel()
    with right:
        st.iframe(html, height=1150)  # 모든 글은 render()에서 이스케이프되어 안전하다


# =====================================================================
# 화면 고르기
# =====================================================================
page = ss.page
if ss.get("_shown_page") != page:
    # 화면이 바뀌면 맨 위부터 보이게 한다 (앞 화면에서 내려간 위치가 남지 않도록)
    ss._shown_page = page
    st.html(
        "<script>for (const el of [document.querySelector('[data-testid=\"stMain\"]'), "
        "document.scrollingElement]) { if (el) el.scrollTo({top: 0}); }</script>",
        unsafe_allow_javascript=True,
    )
if page == "home":
    page_home()
elif page in ("s1", "s2", "s3"):
    page_single(int(page[1]))
elif page in ("f1", "f2", "f3"):
    page_fusion(int(page[1]))
elif page == "tools":
    page_tools()
else:
    page_result()
