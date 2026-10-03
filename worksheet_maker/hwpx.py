"""HWPX(한글 파일)를 읽어 학습지 양식의 구조(문단·표·그림)를 꺼내고, 원본에 가까운 HTML로 그린다.

AI를 쓰지 않는다 — 파일 안의 표(칸 위치·합친 칸·크기·테두리·칠), 글자(크기·굵기·색), 문단(정렬·줄 간격),
그림을 그대로 옮긴다. 글자는 모두 escape한다(양식 파일도 사용자 입력이므로).
단위: HWPUNIT = 1/7200 inch.  mm = v * 25.4 / 7200
"""

from __future__ import annotations

import base64
import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
    "hh": "http://www.hancom.co.kr/hwpml/2011/head",
    "hc": "http://www.hancom.co.kr/hwpml/2011/core",
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "opf": "http://www.idpf.org/2007/opf/",
}


class HwpxError(ValueError):
    pass


def mm(v) -> float:
    return round(float(v) * 25.4 / 7200, 2)


_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_IMG_URI = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/]+=*")


def color(v, default: str = "") -> str:
    """파일에서 읽은 색은 '#rrggbb'만 받는다 (HTML style 속성에 그대로 들어가므로)."""
    return v if isinstance(v, str) and _HEX.fullmatch(v) else default


def _tag(el) -> str:
    return el.tag.split("}")[-1]


# ----- 모델 -----
@dataclass
class Run:
    text: str
    size: float = 10.0  # pt
    bold: bool = False
    italic: bool = False
    underline: bool = False
    color: str = "#000000"
    font: str = ""
    head: bool = False  # 문단 머리(글머리표·자동 번호): 글을 고쳐도 남긴다


@dataclass
class Para:
    runs: list = field(default_factory=list)  # Run | Table | Pic (글자처럼 놓인 표·그림)
    align: str = "left"
    line: float = 1.6
    indent: float = 0.0  # mm (첫 줄)
    left: float = 0.0  # mm
    before: float = 0.0
    after: float = 0.0
    page_break: bool = False

    def text(self) -> str:
        return "".join(r.text for r in self.runs if isinstance(r, Run))


@dataclass
class Border:
    style: str = "none"  # CSS border-style
    width: float = 0.12  # mm
    color: str = "#000000"


@dataclass
class Cell:
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    width: float = 0.0  # mm
    height: float = 0.0
    borders: dict = field(default_factory=dict)  # top/right/bottom/left → Border
    fill: str = ""  # 배경색
    fill_image: str = ""  # 배경 그림 data URI
    valign: str = "middle"
    pad: tuple = (0.5, 1.8, 0.5, 1.8)  # top right bottom left (mm)
    paras: list = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(p.text() for p in self.paras).strip()


@dataclass
class Table:
    rows: int
    cols: int
    cells: list
    col_widths: list = field(default_factory=list)  # mm
    width: float = 0.0


@dataclass
class Pic:
    src: str  # data URI
    width: float
    height: float


@dataclass
class Page:
    width: float = 210.0
    height: float = 297.0
    margin: tuple = (20.0, 20.0, 15.0, 20.0)  # top right bottom left (mm)


@dataclass
class Doc:
    page: Page
    blocks: list  # Para (표·그림은 문단 안의 runs에)


# ----- 읽기 -----
_BORDER_CSS = {"NONE": "none", "SOLID": "solid", "DASH": "dashed", "DOT": "dotted", "DASH_DOT": "dashed",
               "DASH_DOT_DOT": "dashed", "LONG_DASH": "dashed", "CIRCLE": "dotted", "DOUBLE_SLIM": "double",
               "SLIM_THICK": "double", "THICK_SLIM": "double", "SLIM_THICK_SLIM": "double", "WAVE": "solid",
               "DOUBLE_WAVE": "double", "THICK_3D": "solid", "THICK_3D_REVERS": "solid", "3D": "solid", "3D_REVERS": "solid"}
_ALIGN = {"LEFT": "left", "CENTER": "center", "RIGHT": "right", "JUSTIFY": "justify", "DISTRIBUTE": "justify",
          "DISTRIBUTE_SPACE": "justify"}


class _Reader:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.bin = self._manifest()
        head = ET.fromstring(z.read("Contents/header.xml"))
        self.fonts = self._fonts(head)
        self.chars = {c.get("id"): self._char(c) for c in head.iter(f"{{{NS['hh']}}}charPr")}
        self.paras = {p.get("id"): self._para(p) for p in head.iter(f"{{{NS['hh']}}}paraPr")}
        self.fills = {b.get("id"): self._fill(b) for b in head.iter(f"{{{NS['hh']}}}borderFill")}
        self.bullets = {b.get("id"): b.get("char", "•") for b in head.iter(f"{{{NS['hh']}}}bullet")}
        self.numberings = {n.get("id"): {int(h.get("level", 1)): (h.text or "", h.get("numFormat", "DIGIT"), int(h.get("start", 1)))
                                         for h in n.findall("hh:paraHead", NS)}
                           for n in head.iter(f"{{{NS['hh']}}}numbering")}
        self.outline_id = "1"  # 개요 번호에 쓰는 번호 모양 (구역의 outlineShapeIDRef)
        self.counters: dict[str, list[int]] = {}

    def _manifest(self) -> dict:
        items = {}
        try:
            pkg = ET.fromstring(self.z.read("Contents/content.hpf"))
            for it in pkg.iter(f"{{{NS['opf']}}}item"):
                items[it.get("id")] = it.get("href")
        except KeyError:
            pass
        return items

    def image(self, ref: str) -> str:
        href = self.bin.get(ref) or next((n for n in self.z.namelist() if n.startswith(f"BinData/{ref}.")), None)
        if not href:
            return ""
        data = self.z.read(href)
        ext = href.rsplit(".", 1)[-1].lower()
        if ext in ("bmp", "wmf", "emf", "tif", "tiff"):  # 브라우저가 못 그리는 형식은 PNG로
            try:
                from PIL import Image

                out = io.BytesIO()
                Image.open(io.BytesIO(data)).save(out, "PNG")
                data, ext = out.getvalue(), "png"
            except Exception:
                return ""
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext)
        return f"data:image/{mime};base64,{base64.b64encode(data).decode()}" if mime else ""

    @staticmethod
    def _fonts(head) -> dict:
        out = {}
        for ff in head.iter(f"{{{NS['hh']}}}fontface"):
            if ff.get("lang") == "HANGUL":
                for f in ff.iter(f"{{{NS['hh']}}}font"):
                    out[f.get("id")] = f.get("face", "")
        return out

    def _char(self, c) -> dict:
        ref = c.find("hh:fontRef", NS)
        u = c.find("hh:underline", NS)
        return {"size": int(c.get("height", "1000")) / 100, "color": color(c.get("textColor"), "#000000"),
                "bold": c.find("hh:bold", NS) is not None, "italic": c.find("hh:italic", NS) is not None,
                "underline": u is not None and u.get("type", "NONE") not in ("NONE", ""),
                "font": self.fonts.get(ref.get("hangul") if ref is not None else "0", "")}

    @staticmethod
    def _para(p) -> dict:
        al = p.find("hh:align", NS)
        hd = p.find("hh:heading", NS)
        out = {"align": _ALIGN.get(al.get("horizontal") if al is not None else "LEFT", "left"),
               "heading": (hd.get("type", "NONE"), hd.get("idRef", "0"), int(hd.get("level", 0))) if hd is not None else ("NONE", "0", 0),
               "line": 1.6, "indent": 0.0, "left": 0.0, "before": 0.0, "after": 0.0}
        case = p.find("hp:switch/hp:case", NS)
        holder = case if case is not None else p
        m = holder.find("hh:margin", NS)
        if m is not None:
            for k, key in (("intent", "indent"), ("left", "left"), ("prev", "before"), ("next", "after")):
                e = m.find(f"hc:{k}", NS)
                if e is not None:
                    out[key] = mm(e.get("value", "0"))
        ls = holder.find("hh:lineSpacing", NS)
        if ls is not None and ls.get("type", "PERCENT") == "PERCENT":
            out["line"] = max(1.0, int(ls.get("value", "160")) / 100)
        return out

    def _fill(self, b) -> dict:
        borders = {}
        for side in ("top", "right", "bottom", "left"):
            e = b.find(f"hh:{side}Border", NS)
            if e is None:
                borders[side] = Border()
                continue
            w = re.match(r"([\d.]+)", e.get("width", "0.12"))
            style = _BORDER_CSS.get(e.get("type", "NONE"), "solid")
            width = float(w.group(1)) if w else 0.12
            if style == "double":
                width = max(width, 0.5)  # 두 줄이 보이려면 3px쯤 필요
            borders[side] = Border(style, width, color(e.get("color"), "#000000"))
        face, img = "", ""
        wb = b.find("hc:fillBrush/hc:winBrush", NS)
        if wb is not None and wb.get("alpha", "0") in ("0", "0.0"):
            face = color(wb.get("faceColor"), "")
        ib = b.find("hc:fillBrush/hc:imgBrush/hc:img", NS)
        if ib is not None:
            img = ib.get("binaryItemIDRef", "")
        return {"borders": borders, "fill": face, "img": img}

    # --- 본문 ---
    def section(self, name: str) -> tuple[Page, list]:
        root = ET.fromstring(self.z.read(name))
        sp = root.find(".//hp:secPr", NS)
        if sp is not None:
            self.outline_id = sp.get("outlineShapeIDRef", self.outline_id)
        page = Page()
        pp = root.find(".//hp:secPr/hp:pagePr", NS)
        if pp is not None:
            w, h = mm(pp.get("width", "59528")), mm(pp.get("height", "84188"))
            if pp.get("landscape") == "NARROWLY":
                w, h = h, w
            m = pp.find("hp:margin", NS)
            if m is not None:
                page = Page(w, h, (mm(int(m.get("top", 0)) + int(m.get("header", 0))), mm(m.get("right", 0)),
                                   mm(int(m.get("bottom", 0)) + int(m.get("footer", 0))), mm(m.get("left", 0))))
        return page, [self.para(p) for p in root.findall("hp:p", NS)]

    def para(self, p) -> Para:
        st = self.paras.get(p.get("paraPrIDRef"), {})
        out = Para(align=st.get("align", "left"), line=st.get("line", 1.6), indent=st.get("indent", 0.0),
                   left=st.get("left", 0.0), before=st.get("before", 0.0), after=st.get("after", 0.0),
                   page_break=p.get("pageBreak") == "1")
        head = self._head(st.get("heading", ("NONE", "0", 0)))
        if head:
            ch = self.chars.get(next((r.get("charPrIDRef") for r in p.findall("hp:run", NS)), None), {})
            out.runs.append(Run(head + " ", head=True, **ch))
        for run in p.findall("hp:run", NS):
            ch = self.chars.get(run.get("charPrIDRef"), {})
            for el in run:
                t = _tag(el)
                if t == "t":
                    out.runs.append(Run(self._text(el), **ch))
                elif t == "tbl":
                    out.runs.append(self.table(el))
                elif t == "pic":
                    pic = self.pic(el)
                    if pic:
                        out.runs.append(pic)
                elif t in ("rect", "ellipse", "polygon", "curve", "container"):
                    for sub in el.iter(f"{{{NS['hp']}}}subList"):  # 글상자 안의 글
                        for sp in sub.findall("hp:p", NS):
                            out.runs.extend(self.para(sp).runs)
        return out

    def _head(self, heading) -> str:
        """문단 머리: 글머리표('•') 또는 자동 번호('1.', '가.', '①')."""
        kind, ref, level = heading
        if kind == "BULLET":
            ch = self.bullets.get(ref, "•")
            return "•" if not ch or "" <= ch <= "" else ch  # 기호 글꼴(Wingdings)의 글자는 •로
        if kind not in ("NUMBER", "OUTLINE"):
            return ""
        nid = ref if kind == "NUMBER" else self.outline_id
        heads = self.numberings.get(nid)
        if not heads:
            return ""
        lv = level + 1
        cnt = self.counters.setdefault(nid, [0] * 11)
        if cnt[lv] == 0:
            cnt[lv] = heads.get(lv, ("", "DIGIT", 1))[2] - 1
        cnt[lv] += 1
        for k in range(lv + 1, 11):  # 아래 수준은 다시 처음부터
            cnt[k] = 0
        text, fmt, _ = heads.get(lv, ("^%d." % lv, "DIGIT", 1))
        return re.sub(r"\^(\d+)", lambda m: _num(cnt[int(m.group(1))] or 1, heads.get(int(m.group(1)), ("", fmt, 1))[1]), text)

    @staticmethod
    def _text(t) -> str:
        parts = [t.text or ""]
        for ch in t:
            name = _tag(ch)
            parts.append({"tab": "\t", "lineBreak": "\n", "fwSpace": "　", "nbSpace": " "}.get(name, ""))
            parts.append(ch.tail or "")
        return "".join(parts)

    def pic(self, el) -> Pic | None:
        img = el.find(".//hc:img", NS)
        sz = el.find("hp:sz", NS)
        if sz is None or sz.get("width", "0") == "0":
            sz = el.find("hp:curSz", NS)
        if img is None:
            return None
        src = self.image(img.get("binaryItemIDRef", ""))
        if not src:
            return None
        w = mm(sz.get("width", 0)) if sz is not None else 40
        h = mm(sz.get("height", 0)) if sz is not None else 30
        return Pic(src, w, h)

    def table(self, tbl) -> Table:
        cells = []
        for tc in tbl.findall("hp:tr/hp:tc", NS):  # 이 표의 칸만 (안쪽 표는 칸의 문단에서 따로 읽는다)
            if tc.find("hp:cellAddr", NS) is None:
                continue
            addr, span, sz = tc.find("hp:cellAddr", NS), tc.find("hp:cellSpan", NS), tc.find("hp:cellSz", NS)
            margin = tc.find("hp:cellMargin", NS)
            fill = self.fills.get(tc.get("borderFillIDRef"), {"borders": {}, "fill": "", "img": ""})
            sub = tc.find("hp:subList", NS)
            cell = Cell(row=int(addr.get("rowAddr")), col=int(addr.get("colAddr")),
                        rowspan=int(span.get("rowSpan", 1)), colspan=int(span.get("colSpan", 1)),
                        width=mm(sz.get("width", 0)), height=mm(sz.get("height", 0)),
                        borders=fill["borders"], fill=fill["fill"],
                        fill_image=self.image(fill["img"]) if fill["img"] else "",
                        valign={"TOP": "top", "CENTER": "middle", "BOTTOM": "bottom"}.get(
                            sub.get("vertAlign", "CENTER") if sub is not None else "CENTER", "middle"))
            if margin is not None:
                cell.pad = tuple(mm(margin.get(k, 0)) for k in ("top", "right", "bottom", "left"))
            if sub is not None:
                cell.paras = [self.para(p) for p in sub.findall("hp:p", NS)]
            cells.append(cell)
        rows, cols = int(tbl.get("rowCnt", 1)), int(tbl.get("colCnt", 1))
        return Table(rows, cols, cells, _col_widths(cells, cols))


def _num(n: int, fmt: str) -> str:
    if fmt == "HANGUL_SYLLABLE":
        return "가나다라마바사아자차카타파하"[(n - 1) % 14]
    if fmt == "HANGUL_JAMO":
        return "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ"[(n - 1) % 14]
    if fmt in ("CIRCLED_DIGIT", "CIRCLED_DIGIT_EX"):
        return "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"[(n - 1) % 20]
    if fmt == "LATIN_SMALL":
        return "abcdefghijklmnopqrstuvwxyz"[(n - 1) % 26]
    if fmt == "LATIN_CAPITAL":
        return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[(n - 1) % 26]
    if fmt in ("ROMAN_SMALL", "ROMAN_CAPITAL"):
        r = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"][(n - 1) % 10]
        return r.upper() if fmt == "ROMAN_CAPITAL" else r
    return str(n)


def _col_widths(cells: list[Cell], cols: int) -> list[float]:
    """칸들의 (시작 열, 합친 열 수, 너비)로 열 경계를 풀어 열마다 너비를 구한다."""
    b: list[float | None] = [0.0] + [None] * cols
    for _ in range(cols + 2):
        changed = False
        for c in cells:
            a, z = c.col, min(cols, c.col + c.colspan)
            if b[a] is not None and b[z] is None:
                b[z] = b[a] + c.width; changed = True
            elif b[z] is not None and b[a] is None:
                b[a] = b[z] - c.width; changed = True
        if not changed:
            break
    known = [i for i, v in enumerate(b) if v is not None]
    for i in range(cols + 1):  # 못 푼 경계는 앞뒤 사이를 고르게
        if b[i] is None:
            lo = max(k for k in known if k < i); hi = min((k for k in known if k > i), default=None)
            b[i] = b[lo] + ((b[hi] - b[lo]) * (i - lo) / (hi - lo) if hi is not None else 10.0 * (i - lo))
    return [max(1.0, round(b[i + 1] - b[i], 2)) for i in range(cols)]


def read(data: bytes) -> Doc:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise HwpxError("한글(HWPX) 파일을 열 수 없어요. 한글에서 'HWPX'로 저장한 파일인지 확인해 주세요.") from e
    names = sorted(n for n in z.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n))
    if not names or "Contents/header.xml" not in z.namelist():
        raise HwpxError("한글(HWPX) 파일의 내용을 찾지 못했어요.")
    r = _Reader(z)
    page, blocks = None, []
    for n in names:
        pg, bl = r.section(n)
        page = page or pg
        blocks += bl
    return Doc(page or Page(), blocks)


# ----- HTML로 그리기 -----
def _font_stack(name: str) -> str:
    serif = any(k in name for k in ("바탕", "명조", "Batang", "Myeongjo", "궁서"))
    base = "'Noto Serif KR', serif" if serif else "'Noto Sans KR', 'Malgun Gothic', sans-serif"
    safe = re.sub(r"[^\w가-힣 ]", "", name).strip()
    return f"'{safe}', {base}" if safe else base


def _run_html(r: Run) -> str:
    style = [f"font-size:{r.size:g}pt"]
    if r.bold:
        style.append("font-weight:700")
    if r.italic:
        style.append("font-style:italic")
    if r.underline:
        style.append("text-decoration:underline")
    if r.color.lower() not in ("#000000", "#000"):
        style.append(f"color:{r.color}")
    if r.font:
        style.append(f"font-family:{_font_stack(r.font)}")
    text = html.escape(r.text).replace("\t", "&emsp;&emsp;").replace("\n", "<br>")
    return f'<span style="{";".join(style)}">{text}</span>'


def para_html(p: Para) -> str:
    inner = []
    for r in p.runs:
        if isinstance(r, Run):
            inner.append(_run_html(r))
        elif isinstance(r, Table):
            inner.append(table_html(r))
        elif isinstance(r, Pic):
            inner.append(f'<img src="{r.src}" style="width:{r.width}mm;height:{r.height}mm;vertical-align:middle" alt="">')
    size = max((r.size for r in p.runs if isinstance(r, Run)), default=10)
    body = "".join(inner) or f'<span style="font-size:{size:g}pt">&nbsp;</span>'
    # 한글의 '내어쓰기'(들여쓰기 값이 음수): 첫 줄은 왼쪽 여백에, 둘째 줄부터 그만큼 안으로 → CSS로는 여백을 늘리고 첫 줄을 당긴다
    left = p.left - p.indent if p.indent < 0 else p.left
    style = (f"text-align:{p.align};line-height:{p.line:g};margin:{p.before:g}mm 0 {p.after:g}mm {left:g}mm;"
             f"text-indent:{p.indent:g}mm" + (";break-before:page" if p.page_break else ""))
    return f'<div class="hp" style="{style}">{body}</div>'


def _border_css(b: Border | None) -> str:
    if not b or b.style == "none":
        return "none"
    return f"{b.width}mm {b.style} {b.color}"


def table_html(t: Table, rows_range: tuple[int, int] | None = None) -> str:
    """표 전체, 또는 rows_range=(시작 줄, 끝 줄)의 줄만 (양식을 머리·문제 자리로 나눌 때)."""
    cols = "".join(f'<col style="width:{w:g}mm">' for w in t.col_widths)
    by_row: dict[int, list[Cell]] = {}
    for c in t.cells:
        by_row.setdefault(c.row, []).append(c)
    r0, r1 = rows_range or (0, t.rows)
    rows = []
    for r in range(r0, r1):
        tds = []
        for c in sorted(by_row.get(r, []), key=lambda c: c.col):
            rowspan = min(c.rowspan, r1 - c.row)
            style = [f"border-{s}:{_border_css(c.borders.get(s))}" for s in ("top", "right", "bottom", "left")]
            style.append("padding:" + " ".join(f"{v:g}mm" for v in c.pad))
            style.append(f"vertical-align:{c.valign}")
            if rowspan == 1:
                style.append(f"height:{c.height:g}mm")
            if c.fill:
                style.append(f"background:{c.fill}")
            if c.fill_image:
                style.append(f"background-image:url('{c.fill_image}');background-size:100% 100%")
            span = (f' rowspan="{rowspan}"' if rowspan > 1 else "") + (f' colspan="{c.colspan}"' if c.colspan > 1 else "")
            empty = not c.text() and not any(isinstance(r, (Table, Pic)) for p in c.paras for r in p.runs)
            if empty:
                style.append("font-size:0;line-height:0")
            inner = "" if empty else "".join(para_html(p) for p in c.paras)
            tds.append(f'<td{span} style="{";".join(style)}">{inner}</td>')
        rows.append(f"<tr>{''.join(tds)}</tr>")
    width = sum(t.col_widths)
    return (f'<table class="ht" style="width:{width}mm;table-layout:fixed;border-collapse:collapse;margin:0 auto">'
            f"<colgroup>{cols}</colgroup>{''.join(rows)}</table>")


def to_html(doc: Doc, title: str = "양식") -> str:
    pg = doc.page
    body = "".join(para_html(p) for p in doc.blocks)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;700&family=Noto+Serif+KR:wght@400;700&display=swap" rel="stylesheet">
<style>
@page {{ size: {pg.width}mm {pg.height}mm; margin: {pg.margin[0]}mm {pg.margin[1]}mm {pg.margin[2]}mm {pg.margin[3]}mm; }}
html {{ background: #eee; }}
body {{ margin: 0; font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif; font-size: 10pt; color: #000; }}
.sheet {{ width: {pg.width}mm; min-height: {pg.height}mm; box-sizing: border-box; margin: 0 auto; background: #fff;
  padding: {pg.margin[0]}mm {pg.margin[1]}mm {pg.margin[2]}mm {pg.margin[3]}mm; }}
.hp {{ min-height: 1em; word-break: keep-all; overflow-wrap: anywhere; white-space: pre-wrap; }}
td {{ box-sizing: border-box; }}
@media print {{ html {{ background: none; }} .sheet {{ width: auto; min-height: 0; padding: 0; }} }}
</style></head><body><div class="sheet">{body}</div></body></html>"""


# ----- 저장(JSON)·다시 읽기 -----
def to_dict(doc: Doc) -> dict:
    """앱 상태·저장 파일에 담을 수 있는 dict. 문단 안의 글·표·그림은 'k'로 구분한다."""

    def run(r) -> dict:
        if isinstance(r, Table):
            return {"k": "table", "rows": r.rows, "cols": r.cols, "col_widths": r.col_widths,
                    "cells": [cell(c) for c in r.cells]}
        if isinstance(r, Pic):
            return {"k": "pic", "src": r.src, "width": r.width, "height": r.height}
        return {"k": "run", **{f: getattr(r, f) for f in Run.__dataclass_fields__}}

    def para(p: Para) -> dict:
        return {**{f: getattr(p, f) for f in Para.__dataclass_fields__ if f != "runs"}, "runs": [run(r) for r in p.runs]}

    def cell(c: Cell) -> dict:
        out = {f: getattr(c, f) for f in Cell.__dataclass_fields__ if f not in ("paras", "borders")}
        out["pad"] = list(c.pad)
        out["borders"] = {s: vars(b) for s, b in c.borders.items()}
        out["paras"] = [para(p) for p in c.paras]
        return out

    pg = doc.page
    return {"page": {"width": pg.width, "height": pg.height, "margin": list(pg.margin)},
            "blocks": [para(p) for p in doc.blocks]}


def _f(v, lo: float, hi: float, default: float) -> float:
    try:
        return min(hi, max(lo, float(v)))
    except (TypeError, ValueError):
        return default


def from_dict(d: dict) -> Doc:
    """to_dict의 반대. 저장 파일은 사용자가 고칠 수 있으므로 모든 값을 다시 검사한다
    (색은 #rrggbb, 그림은 data URI, 정렬·선 모양은 정해진 값, 숫자는 범위 안)."""
    if not isinstance(d, dict):
        raise HwpxError("양식 정보가 올바르지 않아요.")
    styles = set(_BORDER_CSS.values())
    aligns = set(_ALIGN.values())

    def run(r: dict):
        k = r.get("k")
        if k == "table":
            cells = [cell(c) for c in (r.get("cells") or [])[:3000] if isinstance(c, dict)]
            rows, cols = int(_f(r.get("rows"), 1, 500, 1)), int(_f(r.get("cols"), 1, 100, 1))
            widths = [_f(w, 0.5, 600, 10) for w in (r.get("col_widths") or [])][:cols]
            return Table(rows, cols, cells, widths if len(widths) == cols else _col_widths(cells, cols))
        if k == "pic":
            src = r.get("src", "")
            if not (isinstance(src, str) and _IMG_URI.fullmatch(src)):
                return None
            return Pic(src, _f(r.get("width"), 1, 600, 40), _f(r.get("height"), 1, 900, 30))
        return Run(text=str(r.get("text", "")), size=_f(r.get("size"), 1, 100, 10), bold=bool(r.get("bold")),
                   italic=bool(r.get("italic")), underline=bool(r.get("underline")),
                   color=color(r.get("color"), "#000000"), font=str(r.get("font", ""))[:60], head=bool(r.get("head")))

    def para(p: dict) -> Para:
        runs = [x for x in (run(r) for r in (p.get("runs") or []) if isinstance(r, dict)) if x is not None]
        return Para(runs=runs, align=p.get("align") if p.get("align") in aligns else "left",
                    line=_f(p.get("line"), 0.8, 5, 1.6), indent=_f(p.get("indent"), -200, 200, 0),
                    left=_f(p.get("left"), -50, 200, 0), before=_f(p.get("before"), 0, 100, 0),
                    after=_f(p.get("after"), 0, 100, 0), page_break=bool(p.get("page_break")))

    def border(b) -> Border:
        b = b if isinstance(b, dict) else {}
        return Border(b.get("style") if b.get("style") in styles else "none", _f(b.get("width"), 0, 3, 0.12),
                      color(b.get("color"), "#000000"))

    def cell(c: dict) -> Cell:
        img = c.get("fill_image", "")
        pad = c.get("pad") if isinstance(c.get("pad"), (list, tuple)) and len(c.get("pad")) == 4 else (0.5, 1.8, 0.5, 1.8)
        borders = c.get("borders") if isinstance(c.get("borders"), dict) else {}
        return Cell(row=int(_f(c.get("row"), 0, 500, 0)), col=int(_f(c.get("col"), 0, 100, 0)),
                    rowspan=int(_f(c.get("rowspan"), 1, 500, 1)), colspan=int(_f(c.get("colspan"), 1, 100, 1)),
                    width=_f(c.get("width"), 0, 600, 10), height=_f(c.get("height"), 0, 900, 5),
                    borders={s: border(borders.get(s)) for s in ("top", "right", "bottom", "left")},
                    fill=color(c.get("fill"), ""),
                    fill_image=img if isinstance(img, str) and _IMG_URI.fullmatch(img) else "",
                    valign=c.get("valign") if c.get("valign") in ("top", "middle", "bottom") else "middle",
                    pad=tuple(_f(v, 0, 30, 1) for v in pad),
                    paras=[para(p) for p in (c.get("paras") or []) if isinstance(p, dict)])

    pg = d.get("page") if isinstance(d.get("page"), dict) else {}
    m = pg.get("margin") if isinstance(pg.get("margin"), list) and len(pg.get("margin")) == 4 else [20, 20, 15, 20]
    page = Page(_f(pg.get("width"), 50, 600, 210), _f(pg.get("height"), 50, 900, 297), tuple(_f(v, 0, 100, 15) for v in m))
    return Doc(page, [para(p) for p in (d.get("blocks") or []) if isinstance(p, dict)])


# ----- 양식 나누기: 위에서부터 차례로 놓인 '조각' (문단 하나, 또는 큰 표의 몇 줄) -----
@dataclass
class Unit:
    block: int  # doc.blocks의 번호
    rows: tuple[int, int] | None = None  # 큰 표를 줄 묶음으로 나눈 조각이면 (시작 줄, 끝 줄)


def only_table(p: Para) -> Table | None:
    """글 없이 표 하나만 있는 문단이면 그 표."""
    tables = [r for r in p.runs if isinstance(r, Table)]
    words = "".join(r.text for r in p.runs if isinstance(r, Run) and not r.head).strip()
    return tables[0] if len(tables) == 1 and not words else None


def row_groups(t: Table) -> list[tuple[int, int]]:
    """합친 칸(rowspan)이 걸쳐 있는 줄은 떼지 않고 한 묶음으로."""
    ends: dict[int, int] = {}
    for c in t.cells:
        ends[c.row] = max(ends.get(c.row, c.row + 1), c.row + c.rowspan)
    groups, r = [], 0
    while r < t.rows:
        end, k = ends.get(r, r + 1), r
        while k < end:
            end = max(end, ends.get(k, k + 1))
            k += 1
        end = min(end, t.rows)
        groups.append((r, end))
        r = end
    return groups


def _rows_text(t: Table, rows: tuple[int, int]) -> str:
    return "".join(c.text() for c in t.cells if rows[0] <= c.row < rows[1]).strip()


def units(doc: Doc) -> list[Unit]:
    out = []
    for i, p in enumerate(doc.blocks):
        t = only_table(p)
        groups = row_groups(t) if t is not None else []
        # 글이 든 줄 묶음이 셋 이상인 큰 표(머리와 문제 칸이 한 표에 든 양식)만 줄 단위로 나눈다. 답 줄 표는 통째로.
        if sum(1 for g in groups if _rows_text(t, g)) >= 3:
            out += [Unit(i, g) for g in groups]
        else:
            out.append(Unit(i))
    return out


def walk(p: Para):
    """문단과, 그 안에 든 표 칸의 문단들을 위에서 아래 차례로 (칸 안의 표까지)."""
    yield p
    for r in p.runs:
        if isinstance(r, Table):
            for c in sorted(r.cells, key=lambda c: (c.row, c.col)):
                for cp in c.paras:
                    yield from walk(cp)


def tables_in(p: Para):
    """문단 안의 모든 표 (칸 안의 표까지)."""
    for q in walk(p):
        for r in q.runs:
            if isinstance(r, Table):
                yield r


def unit_cells(doc: Doc, u: Unit) -> list[Cell]:
    """조각 안의 표 칸(위 칸부터). 줄 묶음 조각이면 그 줄의 칸만."""
    p = doc.blocks[u.block]
    if not u.rows:
        return [c for t in tables_in(p) for c in sorted(t.cells, key=lambda c: (c.row, c.col))]
    t = only_table(p)
    out = []
    for c in sorted(t.cells, key=lambda c: (c.row, c.col)):
        if u.rows[0] <= c.row < u.rows[1]:
            out.append(c)
            out += [x for cp in c.paras for t2 in tables_in(cp) for x in sorted(t2.cells, key=lambda c: (c.row, c.col))]
    return out


def unit_paras(doc: Doc, u: Unit) -> list[Para]:
    """조각 안의 문단들 (표 칸 안까지, 위에서 아래 차례)."""
    p = doc.blocks[u.block]
    if not u.rows:
        return list(walk(p))
    t = only_table(p)
    cells = sorted((c for c in t.cells if u.rows[0] <= c.row < u.rows[1]), key=lambda c: (c.row, c.col))
    return [q for c in cells for cp in c.paras for q in walk(cp)]


def unit_text(doc: Doc, u: Unit) -> str:
    return " ".join(t for t in (q.text().strip() for q in unit_paras(doc, u)) if t)


def unit_html(doc: Doc, u: Unit) -> str:
    p = doc.blocks[u.block]
    if not u.rows:
        return para_html(p)
    return f'<div class="hp" style="text-align:{p.align}">{table_html(only_table(p), u.rows)}</div>'
