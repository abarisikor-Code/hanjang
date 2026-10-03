"""한글(HWPX) 학습지 양식: 올린 학습지를 '머리 / 문제 자리 / 꼬리'로 나누고, 문제 자리에 만든 문제를 그 양식의 모양으로 담는다.

AI를 쓰지 않는다. 나누는 곳과 모양 값(글꼴·번호 모양·칸 색·선)은 파일 안의 구조에서 코드가 고르고,
사용자가 화면에서 고칠 수 있다. 문제는 render의 blocks.html.j2로 그리고, 모양 차이는 worksheet.css의
`.df-*`(짜임) `dn-*`(번호) `dsec-*`(소제목) 클래스와 `--df-*` 변수로만 만든다.
"""

from __future__ import annotations

import base64
import copy
import io
import re
from collections import Counter
from typing import Literal

from markupsafe import Markup
from pydantic import BaseModel, Field, field_validator

from . import hwpx

LAYOUTS = {
    "lines": "줄 공책형 — 문제 아래에 답 줄",
    "table": "표형 — 왼쪽 번호 칸 + 오른쪽 내용 칸",
    "boxes": "상자형 — 번호 상자 + 둥근 내용 상자",
}
NUMS = {"dot": "1.", "paren": "1)", "circle": "동그라미 번호", "box": "네모 번호"}
SECS = {"plain": "굵은 글씨만", "bar": "왼쪽 세로 막대", "underline": "밑줄", "box": "색 칸"}

# 머리에서 '이름 칸'처럼 학생이 쓰는 곳, 이 학습지의 정보를 적는 이름표
_FIELD = re.compile(r"이름|성명|학년|\(\s*\)\s*반|\b반\b|번\s*$|초등학교|날짜|모둠|확인")
_HEAD_LABEL = re.compile(r"이름|성명|학년|단원|공부할|학습\s*목표|학습\s*문제|배울\s*내용|성취\s*기준|교과(?!서)|목표")
_FIRST_Q = re.compile(r"^\s*(?:1\s*[.)．]|\(1\)|①)")
_GOAL_LABEL = re.compile(r"공부할|학습\s*목표|학습\s*문제|배울\s*내용|활동\s*목표|^\s*목표")
_UNIT_LABEL = re.compile(r"^\s*단\s*원\s*$")
_QUESTION = re.compile(r"^\s*(?:\d{1,2}\s*[.)．]|\(\d{1,2}\)|[①-⑳])")
_FOOT = re.compile(r"^\s*[※＊*]")
_DECOR = re.compile(r"^([^\w(（\[‘'\"]*)(.*?)([^\w)）\]’'\"?!.]*)$", re.S)  # 제목 앞뒤 꾸밈 기호(☀ ★ …)


class DocStyle(BaseModel):
    """문제 자리의 모양. 값은 양식에서 코드가 읽고(derive_style), 사용자가 고친다."""

    layout: Literal["lines", "table", "boxes"] = "lines"
    font: str = ""  # 글꼴 이름 (없으면 학습지 기본 글꼴)
    size: float = 11.0  # 본문 글자 크기 pt
    num: Literal["dot", "paren", "circle", "box"] = "dot"
    q_bold: bool = False  # 문제 글을 굵게
    label_bg: str = ""  # 번호 칸·소제목 칸 색 (#rrggbb, 없으면 흰색)
    border: str = "#000000"  # 칸 테두리 색
    border_w: float = 0.12  # mm
    line: str = "#000000"  # 답 줄 색
    line_w: float = 0.12  # mm
    line_gap: float = 9.0  # 답 줄 사이 mm
    radius: float = 0.0  # 상자 모서리 mm
    label_w: float = 22.0  # 번호 칸 너비 mm
    sec: Literal["plain", "bar", "underline", "box"] = "plain"
    width: float = 0.0  # 문제 자리 너비 mm (0 = 쪽 너비 가득)

    @field_validator("label_bg", mode="before")
    @classmethod
    def _bg(cls, v: object) -> str:
        return hwpx.color(v, "")

    @field_validator("border", "line", mode="before")
    @classmethod
    def _fg(cls, v: object) -> str:
        return hwpx.color(v, "#000000")

    @field_validator("font", mode="before")
    @classmethod
    def _font(cls, v: object) -> str:
        return re.sub(r"[^\w가-힣 ]", "", str(v or ""))[:40].strip()

    @field_validator("size", "border_w", "line_w", "line_gap", "radius", "label_w", "width", mode="before")
    @classmethod
    def _num(cls, v: object, info) -> float:
        lo, hi, default = {"size": (7, 20, 11), "border_w": (0.05, 1.5, 0.12), "line_w": (0.05, 1.5, 0.12),
                           "line_gap": (5, 20, 9), "radius": (0, 8, 0), "label_w": (8, 60, 22),
                           "width": (0, 400, 0)}[info.field_name]
        return round(hwpx._f(v, lo, hi, default), 2)


class DocForm(BaseModel):
    doc: dict  # hwpx.to_dict 결과. 쓸 때마다 hwpx.from_dict로 값을 다시 검사한다.
    head: int = 0  # 조각 [0, head) = 머리
    foot: int = 0  # 조각 [foot, 끝) = 꼬리.  [head, foot) = 문제 자리 (원래 문제는 지우고 새 문제를 담는다)
    edits: dict[str, str] = Field(default_factory=dict)  # "조각번호.문단번호" → 사용자가 고친 글
    auto_title: bool = True  # 머리의 제목 칸에 학습지 제목을
    auto_goals: bool = True  # '공부할 내용·학습 목표' 칸에 학습 목표를
    repeat_head: bool = False  # 둘째 쪽부터도 머리를 넣는다
    style: DocStyle = Field(default_factory=DocStyle)

    @field_validator("doc", mode="before")
    @classmethod
    def _clean_doc(cls, v: object) -> dict:
        # 저장 파일은 사용자가 고칠 수 있다: 읽을 때마다 정해진 값만 남긴 모양으로 다시 만든다
        return hwpx.to_dict(hwpx.from_dict(v if isinstance(v, dict) else {}))

    def parsed(self) -> hwpx.Doc:
        return hwpx.from_dict(self.doc)


# ----- 처음 읽기: 나눌 곳과 모양 고르기 -----
def from_hwpx(data: bytes) -> DocForm:
    try:
        doc = hwpx.read(data)
    except hwpx.HwpxError:
        raise
    except Exception as e:  # 깨진 XML 등
        raise hwpx.HwpxError("한글 파일을 읽는 중 문제가 생겼어요. 한글에서 HWPX로 다시 저장해 올려 주세요.") from e
    if not doc.blocks:
        raise hwpx.HwpxError("한글 파일에 내용이 없어요.")
    us = hwpx.units(doc)
    head, foot = guess_split(doc, us)
    return DocForm(doc=hwpx.to_dict(doc), head=head, foot=foot, style=derive_style(doc, us, head, foot))


def _first_text(doc: hwpx.Doc, u: hwpx.Unit) -> str:
    return next((t for t in (q.text().strip() for q in hwpx.unit_paras(doc, u)) if t), "")


def guess_split(doc: hwpx.Doc, us: list[hwpx.Unit]) -> tuple[int, int]:
    """머리: 앞쪽에서 이름 칸·'공부할 내용' 같은 이름표가 있는 마지막 조각까지 (없으면 첫 문제 앞까지).
    꼬리: 끝쪽의 '※ …' 안내. 그 뒤의 빈 줄은 꼬리에 넣지 않고 버린다."""
    head = 0
    for i, u in enumerate(us[: max(3, len(us) // 2)]):
        if i and _FIRST_Q.match(_first_text(doc, u)):
            break  # 첫 문제부터는 머리가 아니다
        if _HEAD_LABEL.search(hwpx.unit_text(doc, u)):
            head = i + 1
    if not head:
        head = next((i for i, u in enumerate(us) if _QUESTION.match(_first_text(doc, u))), 0)
    end = len(us)
    while end > head and not hwpx.unit_text(doc, us[end - 1]) and not _has_box(doc, us[end - 1]):
        end -= 1  # 끝의 빈 줄
    foot = end
    while foot - 1 > head and _FOOT.match(_first_text(doc, us[foot - 1])) and not hwpx.unit_cells(doc, us[foot - 1]):
        foot -= 1
    # 꼬리가 없으면 끝까지 문제 자리 (뒤에 이어진 정답지 쪽·빈 줄도 함께 지운다)
    return head, foot if foot < end else len(us)


def _has_box(doc: hwpx.Doc, u: hwpx.Unit) -> bool:
    return any(isinstance(r, (hwpx.Table, hwpx.Pic)) for r in doc.blocks[u.block].runs)


def _avg_color(uri: str) -> str:
    """칸 배경 그림(둥근 상자를 그림 조각으로 만든 양식)의 가운데 색."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGB")
        r, g, b = img.resize((1, 1), Image.BILINEAR).getpixel((0, 0))
        return f"#{r:02X}{g:02X}{b:02X}"
    except Exception:
        return ""


def _light(c: str) -> bool:
    return bool(c) and sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) > 3 * 235


def derive_style(doc: hwpx.Doc, us: list[hwpx.Unit], head: int, foot: int) -> DocStyle:
    body = us[head:foot] or us
    paras = [q for u in body for q in hwpx.unit_paras(doc, u)]
    cells = [c for u in body for c in hwpx.unit_cells(doc, u)]
    st = DocStyle()

    # 글꼴·크기: 문제 자리에서 가장 많이 쓴 것
    weight: Counter = Counter()
    for q in paras:
        for r in q.runs:
            if isinstance(r, hwpx.Run) and r.text.strip() and not r.head:
                weight[(r.font, r.size)] += len(r.text.strip())
    if weight:
        (font, size), _ = weight.most_common(1)[0]
        st.font, st.size = DocStyle._font(font), max(9.0, min(14.0, size))

    # 번호 모양·굵기: 문제 자리의 첫 번호 문단
    for q in paras:
        m = _QUESTION.match(q.text())
        if m:
            mark = m.group(0).strip()
            st.num = "circle" if mark[0] in "①②③④⑤⑥⑦⑧⑨⑩" else "paren" if mark.endswith(")") else "dot"
            st.q_bold = any(r.bold for r in q.runs if isinstance(r, hwpx.Run) and r.text.strip() and not r.head)
            break

    # 짜임: 답 줄 표(글 없이 가로선만) / 둥근 상자를 그림 조각으로 만든 표 / 이름표 칸 + 빈 칸
    def horizontal_only(c: hwpx.Cell) -> bool:
        b = c.borders
        return all(b.get(s) is None or b[s].style == "none" for s in ("left", "right")) and any(
            b.get(s) is not None and b[s].style != "none" for s in ("top", "bottom"))

    line_cells = [c for c in cells if not c.text() and horizontal_only(c) and c.width > 60]
    tiny_img = sum(1 for c in cells if c.fill_image and min(c.width, c.height) < 3)
    labels = [c for c in cells if c.text() and len(c.text()) <= 20 and c.width < 60 and not c.fill_image
              and any(b.style != "none" for b in c.borders.values())]
    labels_img = [c for c in cells if c.text() and len(c.text()) <= 20 and c.fill_image and c.width >= 8]
    if tiny_img >= 8:
        st.layout, st.radius = "boxes", 2.0
    elif any(u.rows for u in body) or len(labels) >= 3 and len(labels) > len(line_cells) / 3:
        st.layout = "table"
    else:
        st.layout = "lines"

    if line_cells:
        b = line_cells[0].borders.get("bottom") or line_cells[0].borders.get("top")
        if b and b.style != "none":
            st.line, st.line_w = b.color, max(0.1, b.width)
        st.line_gap = Counter(round(c.height) for c in line_cells).most_common(1)[0][0]

    # 칸 테두리: 테두리가 있는 칸에서 가장 많이 쓴 선
    edges = Counter((b.color, b.width) for c in cells for b in c.borders.values() if b.style not in ("none", "double"))
    if edges:
        (st.border, w), _ = edges.most_common(1)[0]
        st.border_w = max(0.1, min(w, 0.5))

    # 번호 칸 색: 이름표 칸의 칠 → 없으면 머리 이름표 칸의 칠 → 그림 조각 상자의 색
    def common_fill(cs: list) -> str:
        fills = Counter(c.fill.upper() for c in cs if c.fill and c.fill.upper() != "#FFFFFF")
        return fills.most_common(1)[0][0] if fills else ""

    head_cells = [c for u in us[:head] for c in hwpx.unit_cells(doc, u)]
    img_bg = _avg_color(labels_img[0].fill_image) if labels_img else ""
    st.label_bg = (common_fill(labels) or (img_bg if img_bg != "#FFFFFF" else "")
                   or common_fill([c for c in head_cells if c.text() and len(c.text()) <= 12]))
    if st.layout == "boxes" and st.border == "#000000":
        st.border = "#BBBBBB"  # 그림 조각 상자의 테두리는 보통 옅은 회색

    lab = labels or labels_img
    if lab:
        st.label_w = round(Counter(round(c.width) for c in lab).most_common(1)[0][0])

    # 소제목 모양: [빈 칸(왼쪽 선) | 글] 표 → 세로 막대, 아래 선만 있는 한 칸 표 → 밑줄, 칠한 한 칸 표 → 색 칸
    for u in us:
        for t in hwpx.tables_in(doc.blocks[u.block]):
            if t.rows != 1 or not t.cells:
                continue
            c0 = sorted(t.cells, key=lambda c: c.col)[0]
            if t.cols == 2 and not c0.text() and c0.borders.get("left") and c0.borders["left"].style != "none":
                st.sec = "bar"
            elif t.cols == 1 and c0.text() and len(c0.text()) < 30 and horizontal_only(c0) and st.sec == "plain":
                st.sec = "underline"
        if st.sec == "bar":
            break

    # 문제 자리가 큰 표 안이면 그 표의 너비에 맞춘다
    rows_unit = next((u for u in body if u.rows), None)
    if rows_unit is not None:
        st.width = round(sum(hwpx.only_table(doc.blocks[rows_unit.block]).col_widths), 1)
    if _light(st.border) and st.layout == "table":
        st.border = "#000000"
    return st


# ----- 머리·꼬리 글 고치기 -----
def editable(form: DocForm) -> list[dict]:
    """화면에서 고칠 수 있는 머리·꼬리의 글. slot: 학습지 내용으로 자동으로 채우는 곳(title/unit/goals)."""
    doc = form.parsed()
    us = hwpx.units(doc)
    slots = _slots(doc, us, form.head)
    out = []
    for ui in list(range(form.head)) + list(range(form.foot, len(us))):
        for pi, q in enumerate(hwpx.unit_paras(doc, us[ui])):
            key = f"{ui}.{pi}"
            text = q.text().strip()
            if text or key in slots:
                out.append({"key": key, "text": text, "slot": slots.get(key, ""), "part": "head" if ui < form.head else "foot"})
    return out


def _slots(doc: hwpx.Doc, us: list[hwpx.Unit], head: int) -> dict[str, str]:
    """머리에서 제목 문단, 그 앞의 단원 문단, '공부할 내용' 이름표 옆 칸의 첫 문단, '단원' 옆 칸을 찾는다."""
    slots: dict[str, str] = {}
    cands = []
    for ui in range(head):
        for pi, q in enumerate(hwpx.unit_paras(doc, us[ui])):
            text = q.text().strip()
            runs = [r for r in q.runs if isinstance(r, hwpx.Run) and r.text.strip()]
            if text and runs and not _FIELD.search(text) and not _HEAD_LABEL.search(text) and len(text) <= 60:
                cands.append((f"{ui}.{pi}", max(r.size for r in runs), any(r.bold for r in runs)))
    if cands:
        top = max(s for _, s, _ in cands)
        run: list[str] = []
        for key, size, _ in cands:  # 가장 큰 글씨가 이어진 첫 묶음: 마지막이 제목, 그 앞은 단원
            if size == top:
                run.append(key)
            elif run:
                break
        slots[run[-1]] = "title"
        if len(run) > 1:
            slots[run[-2]] = "unit"

    for ui in range(head):
        cells = hwpx.unit_cells(doc, us[ui])
        for c in cells:
            text = c.text()
            if len(text) > 12:
                continue
            kind = "goals" if _GOAL_LABEL.search(text) else "unit" if _UNIT_LABEL.match(text) else ""
            if not kind:
                continue
            nxt = next((d for d in cells if d.row == c.row and d.col == c.col + c.colspan), None)
            if nxt is None or not nxt.paras:
                continue
            paras = hwpx.unit_paras(doc, us[ui])
            pi = next((i for i, q in enumerate(paras) if q is nxt.paras[0]), None)
            if pi is not None and f"{ui}.{pi}" not in slots:
                slots[f"{ui}.{pi}"] = kind
    return slots


def _set_text(q: hwpx.Para, text: str) -> None:
    """문단의 글을 바꾼다. 첫 글자 모양을 쓰고, 문단 머리(글머리표·번호)와 표·그림은 남긴다."""
    runs = [r for r in q.runs if isinstance(r, hwpx.Run)]
    body = [r for r in runs if not r.head and r.text.strip()] or [r for r in runs if not r.head] or runs
    proto = body[0] if body else hwpx.Run("")
    new = hwpx.Run(text, proto.size, proto.bold, proto.italic, proto.underline, proto.color, proto.font)
    keep = [r for r in q.runs if not isinstance(r, hwpx.Run) or r.head]
    heads = [r for r in keep if isinstance(r, hwpx.Run)]
    q.runs = heads + [new] + [r for r in keep if not isinstance(r, hwpx.Run)]


def _cell_of(doc: hwpx.Doc, u: hwpx.Unit, q: hwpx.Para) -> hwpx.Cell | None:
    return next((c for c in hwpx.unit_cells(doc, u) if c.paras and c.paras[0] is q), None)


def _keep_decor(old: str, new: str) -> str:
    """'☀ 옛 제목 ☀' → '☀ 새 제목 ☀'."""
    m = _DECOR.match(old)
    return f"{m.group(1)}{new}{m.group(3)}" if m and (m.group(1) or m.group(3)) else new


def fill(form: DocForm, ws) -> tuple[hwpx.Doc, list[hwpx.Unit], set[str]]:
    """학습지 내용(제목·단원·목표)과 사용자가 고친 글을 머리·꼬리에 넣은 양식. 넣은 자동 칸 종류도 돌려준다."""
    doc = form.parsed()
    us = hwpx.units(doc)
    slots = _slots(doc, us, form.head)
    used: set[str] = set()
    parts = list(range(form.head)) + list(range(form.foot, len(us)))
    for ui in parts:
        paras = hwpx.unit_paras(doc, us[ui])
        for pi, q in enumerate(paras):
            key = f"{ui}.{pi}"
            if key in form.edits:
                _set_text(q, form.edits[key])
                used.add(slots.get(key, ""))
                continue
            kind = slots.get(key, "")
            if kind == "title" and form.auto_title and ws.title:
                _set_text(q, _keep_decor(q.text().strip(), ws.title))
                used.add(kind)
            elif kind == "unit" and ws.unit:
                _set_text(q, ws.unit)
                used.add(kind)
            elif kind == "goals" and form.auto_goals and ws.goals:
                cell = _cell_of(doc, us[ui], q)
                if cell is None:
                    continue
                proto = cell.paras[0]
                new = []
                for g in ws.goals:
                    p = copy.deepcopy(proto)
                    _set_text(p, re.sub(r"\*\*|\[\[|\]\]", "", g))
                    new.append(p)
                cell.paras = new
                used.add(kind)
    return doc, us, used


# ----- 그리기 -----
def _font_stack(name: str) -> str:
    return hwpx._font_stack(name) if name else "var(--font-body)"


def css_vars(st: DocStyle) -> str:
    return (f"--base: {st.size:g}pt; --line-h0: {st.line_gap:g}mm; --font-body: {_font_stack(st.font)}; "
            f"--font-head: {_font_stack(st.font)}; --df-lbg: {st.label_bg or '#ffffff'}; --df-bc: {st.border}; "
            f"--df-bw: {st.border_w:g}mm; --df-lc: {st.line}; --df-lw2: {st.line_w:g}mm; --df-r: {st.radius:g}mm; "
            f"--df-lw: {st.label_w:g}mm; --df-qw: {700 if st.q_bold else 400}")


def classes(st: DocStyle) -> str:
    return f"df df-{st.layout} dn-{st.num} dsec-{st.sec}"


def parts(form: DocForm, ws) -> dict:
    """render가 쪽 틀을 만들 때 쓰는 조각들 (모두 escape된 HTML)."""
    doc, us, used = fill(form, ws)
    head = "".join(hwpx.unit_html(doc, u) for u in us[: form.head])
    foot_units = us[form.foot:]
    foot = "".join(hwpx.unit_html(doc, u) for u in foot_units) if any(
        hwpx.unit_text(doc, u) or _has_box(doc, u) for u in foot_units) else ""
    pg = doc.page
    st = form.style
    # 문제 자리가 큰 표의 아랫부분이면 그 표의 바깥 테두리를 이어 그린다
    joined = st.layout == "table" and form.head > 0 and us[form.head - 1].rows is not None if us else False
    width = f"width:{st.width:g}mm;margin-left:auto;margin-right:auto;" if st.width else ""
    return {
        "head": Markup(head),
        "foot": Markup(foot),
        "page": f"width:{pg.width:g}mm;height:{pg.height:g}mm;padding:{pg.margin[0]:g}mm {pg.margin[1]:g}mm "
                f"{pg.margin[2]:g}mm {pg.margin[3]:g}mm",
        "size": f"{pg.width:g}mm {pg.height:g}mm",
        "box_class": classes(st) + (" df-joined" if joined else ""),
        "box_class_next": classes(st) + (" df-joined" if joined and form.repeat_head else ""),
        "box_style": width + css_vars(st),
        "repeat_head": form.repeat_head,
        "has_title": "title" in used,
        "has_goals": "goals" in used,
    }


def split_preview(form: DocForm) -> str:
    """나눈 곳을 보여 주는 HTML: 머리(파랑)·문제 자리(빨강, 지우고 새 문제를 넣는 곳)·꼬리(초록)."""
    doc = form.parsed()
    us = hwpx.units(doc)
    out = []
    colors = {"head": ("#1e5adc", "머리 (그대로 둠)"), "body": ("#dc1e1e", "문제 자리 (새 문제로 바뀜)"),
              "foot": ("#1e9a46", "꼬리 (그대로 둠)")}
    prev = None
    for i, u in enumerate(us):
        part = "head" if i < form.head else "foot" if i >= form.foot else "body"
        if part != prev:
            if prev:
                out.append("</div>")
            c, label = colors[part]
            out.append(f'<div class="pv" style="border-color:{c}"><span class="pv-tag" style="background:{c}">{label}</span>')
            prev = part
        out.append(f'<div class="pv-unit" title="{i + 1}번 조각">{hwpx.unit_html(doc, u)}</div>')
    if prev:
        out.append("</div>")
    pg = doc.page
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&family=Noto+Serif+KR:wght@400;700&display=swap" rel="stylesheet">
<style>
html {{ background: #eee; }} body {{ margin: 0; font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif; font-size: 10pt; }}
.sheet {{ width: {pg.width:g}mm; box-sizing: border-box; margin: 0 auto; background: #fff;
  padding: {pg.margin[0]:g}mm {pg.margin[1]:g}mm {pg.margin[2]:g}mm {pg.margin[3]:g}mm; zoom: var(--fit, 1); }}
.hp {{ min-height: 1em; word-break: keep-all; overflow-wrap: anywhere; white-space: pre-wrap; }}
td {{ box-sizing: border-box; }}
.pv {{ position: relative; border: 2px dashed; margin: 3mm -2mm; padding: 5mm 1.5mm 1.5mm; }}
.pv-tag {{ position: absolute; top: -1px; left: -1px; color: #fff; font: 700 11px/1 sans-serif; padding: 4px 8px; }}
</style></head><body><div class="sheet">{''.join(out)}</div>
<script>
function fit() {{ document.documentElement.style.setProperty("--fit", Math.min(1, (window.innerWidth - 8) / {pg.width * 3.78:.0f}).toFixed(3)); }}
fit(); window.addEventListener("resize", fit);
</script></body></html>"""


def unit_labels(form: DocForm) -> list[str]:
    """나눌 곳을 고르는 목록에 보일 조각 이름."""
    doc = form.parsed()
    out = []
    for i, u in enumerate(hwpx.units(doc)):
        text = hwpx.unit_text(doc, u)
        kind = "표" if (u.rows or _has_box(doc, u)) else "글"
        out.append(f"{i + 1}. [{kind}] {text[:28] + ('…' if len(text) > 28 else '') if text else '(빈 칸)'}")
    return out
