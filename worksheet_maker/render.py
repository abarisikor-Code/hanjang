"""학습지 JSON → A4 인쇄용 HTML. AI는 관여하지 않는, 항상 같은 결과를 내는 조판 엔진."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from . import docform
from .diagrams import render_svg
from .forms import Form
from .saved import pack
from .schema import IMAGE_SIZES, QUESTION_TYPES, Worksheet
from .theme import Theme, body_classes, css_vars

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 대상 학년별 조판 값: 본문 크기(pt), 줄 간격, 답 줄 높이(mm). 한장은 초등학교용이다.
LEVELS: dict[str, dict] = {
    "lower": {"label": "초등 1~2학년", "base": 13, "lh": 1.85, "line_h": 11},
    "elementary": {"label": "초등 3~4학년", "base": 12, "lh": 1.8, "line_h": 10},
    "upper": {"label": "초등 5~6학년", "base": 11.5, "lh": 1.75, "line_h": 9.5},
}
# 예전에 저장한 파일의 학교급 (초등 3~6학년 = elementary, 중·고등학교 = 5~6학년 모양으로)
_LEVEL_ALIASES = {"middle": "upper", "high": "upper"}


def level_key(level: str | None) -> str:
    level = _LEVEL_ALIASES.get(level or "", level or "")
    return level if level in LEVELS else "elementary"


def grade_level(grade: int) -> str:
    return "lower" if grade <= 2 else "elementary" if grade <= 4 else "upper"

PER_PAGE = 10  # 한 쪽에 문제 10개까지 (초등학생이 풀 공간이 있어야 한다)


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_BLANK = re.compile(r"\[\[(.*?)\]\]")
_OPTION_NUM = re.compile(r"^\s*(?:[①-⑩]|\(?\d{1,2}[).](?!\d)|[가-하][.)])\s*")  # '0.8' 같은 소수는 번호가 아니다


# 수식: AI가 LaTeX로 쓰거나(\frac{2}{6}, $…$, \times) 2/6처럼 써도 교과서처럼 보이게 바꾼다.
_TEX_SYMBOLS = {
    "times": "×", "div": "÷", "cdot": "·", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "pm": "±", "pi": "π", "circ": "°", "square": "□", "triangle": "△", "angle": "∠", "rightarrow": "→", "to": "→",
}
_TEX_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_PLAIN_FRAC = re.compile(r"(?<![A-Za-z0-9/.])(\d{1,4})/(\d{1,4})(?![A-Za-z0-9/])")  # '1/3을'처럼 조사가 붙어도


def _frac(num: str, den: str) -> str:
    return f'<span class="frac"><span class="num">{num.strip()}</span><span class="den">{den.strip()}</span></span>'


def _math(html: str) -> str:
    """이스케이프된 글 안의 수식 표기를 HTML로 바꾼다 (새로 생기는 태그는 분수 span과 sup뿐)."""
    if "\\" in html or "$" in html:
        html = re.sub(r"\$([^$]{1,200})\$", r"\1", html)  # $…$ 수식 구분 기호 지우기
        html = re.sub(r"\\text\{([^{}]*)\}", r"\1", html)
        html = re.sub(r"\\(?:left|right)(?=[()\[\]|])|\\[,;!]\s?", "", html)
        html = _TEX_FRAC.sub(lambda m: _frac(m.group(1), m.group(2)), html)
        html = re.sub(r"\^\s*\{?\\circ\}?", "°", html)
        html = re.sub(r"\\([a-zA-Z]+)", lambda m: _TEX_SYMBOLS.get(m.group(1), m.group(0)), html)
    html = re.sub(r"(?<=[\w)])\^\{?(\d{1,2})\}?", r"<sup>\1</sup>", html)  # cm^2 → cm²
    return _PLAIN_FRAC.sub(lambda m: _frac(m.group(1), m.group(2)), html)


def _inline(text: str) -> Markup:
    """HTML을 이스케이프한 뒤 **굵게**와 분수·수식 기호만 허용한다."""
    return Markup(_math(_BOLD.sub(r"<strong>\1</strong>", str(escape(text)))))


def _inline_blanks(text: str) -> Markup:
    """한 줄 글 + [[정답]] 빈칸 (연습 문제·노트 칸)."""
    return Markup(_BLANK.sub(_blank_span, str(_inline(text))))


def _paras(text: str, blanks: bool = False) -> Markup:
    """빈 줄은 문단, 한 줄 바꿈은 <br>로. blanks=True면 [[정답]]을 빈칸으로 바꾼다."""
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        if not para.strip():
            continue
        html = _inline(para)
        if blanks:
            html = Markup(_BLANK.sub(_blank_span, str(html)))
        out.append("<p>" + str(html).replace("\n", "<br>") + "</p>")
    return Markup("".join(out))


def _blank_span(m: re.Match) -> str:
    # 정답 길이에 맞춰 빈칸 폭을 정하되, 답이 드러나지 않게 내용은 넣지 않는다.
    answer = re.sub(r"<[^>]+>", "", m.group(1))  # 분수 등으로 바뀐 태그는 길이에서 뺀다
    width = max(4, min(len(answer) * 1.3 + 2, 18))
    return f'<span class="blank" style="min-width:{width:.1f}em"></span>'


def _css_string(text: str) -> Markup:
    """<style> 안의 CSS content: 문자열로 안전하게 넣는다."""
    s = re.sub(r"\s+", " ", text or "").strip()
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\3C ")
    return Markup(f'"{s}"')


def _option_text(text: str) -> str:
    return _OPTION_NUM.sub("", text, count=1)


def _option_cols(options: list[str]) -> int:
    longest = max((len(o) for o in options), default=0)
    if longest <= 8:
        return min(len(options), 5)
    if longest <= 22:
        return 2
    return 1


def _concept_item(text: str) -> tuple[str, str]:
    term, sep, desc = text.partition(":")
    return (term.strip(), desc.strip()) if sep and len(term) <= 30 else ("", text.strip())


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(
        inline=_inline,
        blanks=_inline_blanks,
        paras=_paras,
        css_string=_css_string,
        svg=render_svg,
        option_text=_option_text,
    )
    env.globals.update(CIRCLED=CIRCLED, IMAGE_SIZES=IMAGE_SIZES, option_cols=_option_cols, concept_item=_concept_item)
    return env


def render(
    ws: Worksheet, level: str = "elementary", footer: str = "", theme: Theme | None = None, show_answers: bool = False,
    show_print_button: bool = True,
    thumbnail: bool = False,  # 학습지 모양 고르기용 작은 미리보기 (좁은 종이)
    form: Form | None = None,  # 내 학습지 양식: 이 양식 쪽 그림 위의 칸에 내용을 담는다
    edit: bool = False,  # 앱 미리보기 전용: 학습지 위에서 바로 고치는 도구(끌어서 옮기기·도구 막대)를 넣는다
    per_page: int = PER_PAGE,  # 한 쪽에 문제 몇 개까지 (0이면 나누지 않는다). 초등학생이 풀 공간을 두려고.
) -> str:
    lv = dict(LEVELS[level_key(level)])
    theme = theme or Theme()
    if theme.size == "large":  # 글자 크기 '크게': 본문과 답 칸을 함께 키운다
        lv["base"] = round(lv["base"] * 1.15, 1)
        lv["line_h"] = round(lv["line_h"] * 1.12, 1)

    # 번호는 AI가 아니라 코드가 붙인다: 문항 1, 2 … / 활동 1, 2 … / 그림 1, 2 …
    numbered = []
    q_num = act_num = fig_num = 0
    for b in ws.blocks:
        num = 0
        if b.type in QUESTION_TYPES:
            q_num += 1
            num = q_num
        elif b.type == "activity":
            act_num += 1
            num = act_num
        elif b.type in ("image", "diagram"):
            fig_num += 1
            num = fig_num
        numbered.append((b, num))
    extras = _extras(ws, numbered)
    form = None if thumbnail else form
    # 한 쪽에 문제 몇 개까지: 쪽 나눔은 인쇄 크기로 재어야 해서 HTML 안의 스크립트가 한다 (연습 문제지·놀이는 촘촘한 그대로)
    paged = int(per_page) if per_page and not thumbnail and ws.kind not in ("drill", "game") else 0
    editable = edit and not thumbnail

    def block_attrs(i: int) -> Markup:
        """블록 맨 바깥 태그에 붙일 속성: 학습지 위에서 고치기용 번호(data-bi)."""
        return Markup(f' data-bi="{int(i)}"') if editable else Markup("")

    running = " · ".join(x for x in (ws.subject, ws.title) if x)
    doc = docform.parts(form.doc, ws) if form and form.doc else None  # 한글 양식: 머리·꼬리 HTML과 문제 자리 모양
    form_pages = [_form_page(p) for p in form.pages] if form and not doc else []
    return _env().get_template("worksheet.html.j2").render(
        ws=ws,
        blocks=numbered,
        extras=extras,
        lv=lv,
        running=running,
        footer=footer,
        theme_vars=Markup(css_vars(theme)),
        theme_classes=f"{body_classes(theme)} wt-{ws.kind}",
        show_answers=show_answers,
        show_print_button=show_print_button and not thumbnail,
        thumbnail=thumbnail,
        editable=editable,
        block_attrs=block_attrs,
        per_page=paged,
        form_pages=form_pages,
        doc=doc,
        form_has_title=doc["has_title"] if doc else any(r.kind == "title" for p in (form.pages if form else []) for r in p.regions),
        saved_data=pack(ws, theme, level, footer, show_answers, form),  # 이 HTML을 앱에 다시 올리면 편집 상태로 열린다
    )


def _extras(ws: Worksheet, numbered: list) -> list[dict]:
    """학습지 유형에 따라 코드가 정하는 값: 단원평가 배점, 연습 문제 칸 수, 빙고판."""
    extras: list[dict] = [{} for _ in numbered]
    if ws.kind == "test":  # 100점을 문항 수로 나누고, 남는 점수는 뒤쪽(어려운) 문항에 1점씩
        qs = [i for i, (b, num) in enumerate(numbered) if b.type in QUESTION_TYPES]
        if qs:
            base, rest = divmod(100, len(qs))
            for k, i in enumerate(qs):
                extras[i]["pts"] = base + (1 if k >= len(qs) - rest else 0)
    for i, (b, _) in enumerate(numbered):
        if b.type == "drill":
            longest = max((len(_BLANK.sub(lambda m: "□" * max(2, len(m.group(1))), it)) for it in b.items), default=0)
            extras[i]["cols"] = 4 if longest <= 12 else 3 if longest <= 18 else 2 if longest <= 32 else 1
        elif b.type == "bingo":
            extras[i]["grid"] = _bingo_grid(ws, b)
    return extras


def _bingo_grid(ws: Worksheet, bingo) -> list[list[str]]:
    """빙고판: 학습지 문항의 정답을 모두 넣고 AI가 준 오답으로 채운 뒤 섞는다 (같은 학습지는 늘 같은 판)."""
    import random
    import zlib

    def clean(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"^\(?\d+\)\s*", "", text)).strip()

    answers: list[str] = []
    for b in ws.blocks:
        if b.type == "fill_blank":
            answers += [clean(a) for a in re.findall(r"\[\[(.*?)\]\]", b.body)]
        elif b.type in ("short_answer", "multiple_choice") and b.answer_text():
            text = clean(b.answer_text())
            if b.type == "multiple_choice":
                text = _option_text(text)
            answers.append(text)
    answers = [a for a in dict.fromkeys(answers) if a and len(a) <= 14]
    wrong = [w for w in dict.fromkeys(clean(x) for x in bingo.items) if w and w not in answers and len(w) <= 14]
    # 정답이 판의 2/3을 넘지 않게 (정답만 가득하면 놀이가 되지 않는다)
    size = 3 if len(answers) <= 5 else 4 if len(answers) <= 10 else 5
    cells = (answers + wrong)[: size * size]
    cells += ["★"] * (size * size - len(cells))  # 칸이 모자라면 '자유 칸'
    random.Random(zlib.crc32(ws.title.encode("utf-8"))).shuffle(cells)
    return [cells[r * size:(r + 1) * size] for r in range(size)]


def _form_page(page) -> dict:
    """양식 한 쪽의 위치 값(mm·%)을 코드가 계산해 둔다. 쪽 그림 자체는 저장 정보(JSON)에서 스크립트가 넣는다."""
    left, top, w, h = page.canvas_mm()
    regions = []
    for r in page.regions:
        y0, x0, y1, x1 = r.box
        style = f"top:{y0 / 10:g}%;left:{x0 / 10:g}%;height:{(y1 - y0) / 10:g}%;width:{(x1 - x0) / 10:g}%"
        regions.append({"kind": r.kind, "erase": r.erase, "style": style})
    return {"canvas": f"left:{left:g}mm;top:{top:g}mm;width:{w:g}mm;height:{h:g}mm", "regions": regions}


def main() -> None:
    p = argparse.ArgumentParser(description="학습지 JSON을 A4 HTML로 만듭니다 (AI 없이 조판만).")
    p.add_argument("json_file")
    p.add_argument("-o", "--out", help="저장할 HTML 경로 (기본: JSON과 같은 이름)")
    p.add_argument("--level", choices=list(LEVELS), default="elementary")
    p.add_argument("--footer", default="")
    p.add_argument("--theme", help="스타일 JSON 경로 (예: themes/교과서형.json)")
    args = p.parse_args()

    src = Path(args.json_file)
    ws = Worksheet.model_validate_json(src.read_text(encoding="utf-8"))
    theme = Theme.model_validate_json(Path(args.theme).read_text(encoding="utf-8")) if args.theme else None
    out = Path(args.out) if args.out else src.with_suffix(".html")
    out.write_text(render(ws, level=args.level, footer=args.footer, theme=theme), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
