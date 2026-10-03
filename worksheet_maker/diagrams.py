"""수학 도식 그리기 (무료, AI 그림 모델 불필요).

AI는 도식의 '설계도'(spec: 종류와 숫자)만 정하고, 이 모듈이 언제나 같은 규칙으로 정확한 흑백 SVG를 그린다.
예: {"kind": "groups", "groups": 16, "per_group": 4} → 4개씩 16묶음, 정확히 64개.

새 도식을 추가할 때: KINDS에 설명과 기본값을 넣고, _draw_<kind> 함수를 만든다.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from html import escape

from markupsafe import Markup

INK = "#111"
SHADE = "#bdbdbd"  # 흑백 레이저 프린터에서 옅은 회색으로 나오는 색

# 종류: (한국어 이름, AI·사용자용 형식 설명, 기본값)
KINDS: dict[str, tuple[str, str, dict]] = {
    "groups": ("묶음 그림", "곱셈·나눗셈. groups(묶음 수 1~20), per_group(묶음마다 개수 1~20), "
               "item(circle|square|star|triangle 또는 상황에 맞는 이모지 하나, 예: 🌰 ⚽ 🍎), "
               "group_label(묶음 아래 글자, 선택)", {"groups": 3, "per_group": 4, "item": "circle", "group_label": ""}),
    "array": ("배열", "곱셈의 배열. rows(줄 1~15), cols(칸 1~15)", {"rows": 3, "cols": 5}),
    "number_line": ("수직선", "start, end(end-start ≤ 100), step(눈금 숫자 간격), jumps(뛰어 세기 점 목록, 예: [0,3,6,9]), "
                    "marks(점 찍을 수 목록), hidden(숫자 대신 □로 보일 수 목록)",
                    {"start": 0, "end": 20, "step": 5, "jumps": [], "marks": [], "hidden": []}),
    "fraction": ("분수 그림", "style(bar|circle), denominator(분모 2~12), numerator(색칠할 칸 수, 분모의 4배까지 → 여러 개 그림)",
                 {"style": "bar", "denominator": 4, "numerator": 3}),
    "clock": ("시계", "hour(1~12), minute(0~59), show_hands(false면 바늘 없는 빈 시계)", {"hour": 3, "minute": 30, "show_hands": True}),
    "bar_chart": ("막대그래프", "title, labels(항목 이름 목록, 10개 이하), values(수 목록), unit(단위, 예: 명)",
                  {"title": "", "labels": ["사과", "배", "귤"], "values": [5, 3, 7], "unit": "명"}),
    "line_chart": ("꺾은선그래프", "title, labels(가로축 이름 목록), values(수 목록), unit(단위, 예: °C)",
                   {"title": "", "labels": ["1월", "2월", "3월", "4월"], "values": [2, 5, 9, 14], "unit": "°C"}),
    "shape": ("도형", "shape(triangle|right_triangle|equilateral|isosceles|square|rectangle|parallelogram|trapezoid|rhombus|"
              "polygon|circle), sides(polygon의 변 수 3~10), side_labels(변 길이 글자 목록, 예: ['5 cm','3 cm']), "
              "radius_label(circle의 반지름 글자)", {"shape": "rectangle", "sides": 5, "side_labels": [], "radius_label": ""}),
    "angle": ("각도", "degrees(1~359), show_value(false면 각도 자리에 □)", {"degrees": 60, "show_value": True}),
    "scene": ("그림 카드", "장면을 보여 주는 간단한 흑백 그림. emojis(이모지 목록 1~6개, 예: ['🏪','🍎','🧒']), "
              "labels(각 그림 아래 글자 목록), counts(각 그림을 몇 개 그릴지 1~10, 생략하면 1)",
              {"emojis": ["🏪", "🍎", "🧒"], "labels": ["가게", "사과", "민수"], "counts": [1, 3, 1]}),
    "pictograph": ("그림그래프", "그림그래프. labels(항목 이름), values(수), emoji(그림 하나, 예: 🙂), big(큰 그림 하나가 나타내는 수, 기본 10), "
                   "unit(단위, 예: 명). 작은 그림 하나는 1(또는 big이 100이면 10)",
                   {"labels": ["가", "나", "다"], "values": [23, 15, 31], "emoji": "🙂", "big": 10, "unit": "명"}),
    "base_ten": ("수 모형", "자릿값. hundreds(백 모형 0~9), tens(십 모형 0~9), ones(일 모형 0~9)", {"hundreds": 1, "tens": 3, "ones": 5}),
}


# 글자 칸 중 정해진 값만 받는 것 (편집 화면에서 고르기 상자로 보여 준다): 값 → 한국어 이름
CHOICES: dict[str, dict[str, str]] = {
    "item": {"circle": "●", "square": "■", "star": "★", "triangle": "▲"},
    "style": {"bar": "막대", "circle": "원"},
    "big": {10: "10", 100: "100"},
    "shape": {
        "triangle": "삼각형", "right_triangle": "직각삼각형", "equilateral": "정삼각형", "isosceles": "이등변삼각형",
        "square": "정사각형", "rectangle": "직사각형", "parallelogram": "평행사변형", "trapezoid": "사다리꼴",
        "rhombus": "마름모", "polygon": "정다각형", "circle": "원",
    },
}

# 편집 화면에 보일 칸 이름
PARAM_LABELS = {
    "groups": "묶음 수", "per_group": "묶음마다 개수", "item": "모양", "group_label": "묶음 아래 글자",
    "rows": "줄 수", "cols": "칸 수", "start": "시작 수", "end": "끝 수", "step": "숫자 쓰는 간격",
    "jumps": "뛰어 세기 (쉼표로)", "marks": "점 찍을 수 (쉼표로)", "hidden": "□로 가릴 수 (쉼표로)",
    "style": "모양", "denominator": "분모 (똑같이 나눈 수)", "numerator": "색칠할 칸 수", "hour": "시", "minute": "분",
    "show_hands": "시곗바늘 보이기", "title": "제목", "labels": "항목 이름 (쉼표로)", "values": "수 (쉼표로)", "unit": "단위",
    "shape": "도형", "sides": "변의 수 (정다각형)", "side_labels": "변 길이 글자 (쉼표로)", "radius_label": "반지름 글자",
    "degrees": "각도(°)", "show_value": "각도 숫자 보이기", "hundreds": "백 모형", "tens": "십 모형", "ones": "일 모형",
    "emojis": "그림 (이모지, 쉼표로)", "counts": "그림 개수 (쉼표로)", "emoji": "그림 (이모지 하나)", "big": "큰 그림 하나의 수",
}


def _is_emoji(v) -> bool:
    """이모지 한 개(변형 선택자·합자 포함)인지. 한글·영문·숫자는 아니다."""
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > 8:
        return False
    return unicodedata.category(v.strip()[0]) in ("So", "Sk")


def guide() -> str:
    """AI 프롬프트에 넣는 도식 형식 설명."""
    lines = [f'- {k} ({name}): {desc}' for k, (name, desc, _) in KINDS.items()]
    return "\n".join(lines)


# ----- 설계도 정리 (AI·사용자가 준 값을 안전한 범위로) -----
def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def _nums(v, limit=30) -> list[float]:
    out = []
    for x in (v if isinstance(v, list) else []):
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            continue
    return out[:limit]


def _strs(v, limit=12, length=20) -> list[str]:
    return [str(x)[:length] for x in (v if isinstance(v, list) else [])][:limit]


def normalize(spec) -> dict:
    """문자열(JSON)이나 dict를 받아 그릴 수 있는 설계도로 정리한다. 알 수 없으면 {}."""
    if isinstance(spec, str):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", spec.strip())
        try:
            spec = json.loads(text) if text else {}
        except json.JSONDecodeError:
            return {}
    if not isinstance(spec, dict) or spec.get("kind") not in KINDS:
        return {}
    kind = spec["kind"]
    d = {**KINDS[kind][2], **{k: v for k, v in spec.items() if v is not None}}
    if kind == "groups":
        g, p = _int(d["groups"], 1, 20, 3), _int(d["per_group"], 1, 20, 4)
        item = d.get("item") if d.get("item") in ("circle", "square", "star", "triangle") or _is_emoji(d.get("item")) else "circle"
        item = item.strip()
        return {"kind": kind, "groups": g, "per_group": p, "item": item, "group_label": str(d.get("group_label") or "")[:12]}
    if kind == "array":
        return {"kind": kind, "rows": _int(d["rows"], 1, 15, 3), "cols": _int(d["cols"], 1, 15, 5)}
    if kind == "number_line":
        start = _int(d["start"], -1000, 100000, 0)
        end = _int(d["end"], start + 1, start + 100, start + 10)
        step = _int(d["step"], 1, 100, max(1, (end - start) // 10))
        inside = lambda xs: [x for x in xs if start <= x <= end]  # noqa: E731
        return {"kind": kind, "start": start, "end": end, "step": step, "jumps": inside(_nums(d.get("jumps")))[:20],
                "marks": inside(_nums(d.get("marks"))), "hidden": inside(_nums(d.get("hidden")))}
    if kind == "fraction":
        den = _int(d["denominator"], 2, 12, 4)
        return {"kind": kind, "style": "circle" if d.get("style") == "circle" else "bar", "denominator": den,
                "numerator": _int(d["numerator"], 0, den * 4, 1)}
    if kind == "clock":
        return {"kind": kind, "hour": _int(d["hour"], 1, 12, 3), "minute": _int(d["minute"], 0, 59, 0),
                "show_hands": d.get("show_hands") not in (False, "false", 0, "0")}
    if kind in ("bar_chart", "line_chart"):
        labels, values = _strs(d.get("labels"), 10), _nums(d.get("values"), 10)
        n = min(len(labels), len(values))
        if n == 0:
            return {}
        return {"kind": kind, "title": str(d.get("title") or "")[:30], "labels": labels[:n],
                "values": [max(0.0, v) for v in values[:n]], "unit": str(d.get("unit") or "")[:6]}
    if kind == "shape":
        shapes = ("triangle", "right_triangle", "equilateral", "isosceles", "square", "rectangle", "parallelogram",
                  "trapezoid", "rhombus", "polygon", "circle")
        return {"kind": kind, "shape": d.get("shape") if d.get("shape") in shapes else "rectangle",
                "sides": _int(d.get("sides"), 3, 10, 5), "side_labels": _strs(d.get("side_labels"), 10, 12),
                "radius_label": str(d.get("radius_label") or "")[:12]}
    if kind == "angle":
        return {"kind": kind, "degrees": _int(d["degrees"], 1, 359, 60), "show_value": d.get("show_value") not in (False, "false", 0, "0")}
    if kind == "scene":
        # 예시 기본값이 섞이지 않도록 받은 값만 쓰고, 그림·글자·개수를 짝지어 함께 거른다.
        raw = spec.get("emojis") if isinstance(spec.get("emojis"), list) else []
        labels = _strs(spec.get("labels"), 6, 16)  # '우유 2L 300mL'처럼 단위가 잘리지 않게 넉넉히
        counts = spec.get("counts") if isinstance(spec.get("counts"), list) else []
        rows = [(e.strip(), labels[i] if i < len(labels) else "", _int(counts[i], 1, 10, 1) if i < len(counts) else 1)
                for i, e in enumerate(raw[:6]) if _is_emoji(e)]
        if not rows:
            return {}
        return {"kind": kind, "emojis": [r[0] for r in rows], "labels": [r[1] for r in rows], "counts": [r[2] for r in rows]}
    if kind == "pictograph":
        labels, values = _strs(d.get("labels"), 6), _nums(d.get("values"), 6)
        n = min(len(labels), len(values))
        if n == 0:
            return {}
        big = 100 if _int(d.get("big"), 1, 1000, 10) >= 100 else 10
        return {"kind": kind, "labels": labels[:n], "values": [int(max(0, min(v, big * 9 + big - 1))) for v in values[:n]],
                "emoji": d.get("emoji").strip() if _is_emoji(d.get("emoji")) else "🙂", "big": big, "unit": str(d.get("unit") or "")[:6]}
    if kind == "base_ten":
        return {"kind": kind, "hundreds": _int(d["hundreds"], 0, 9, 1), "tens": _int(d["tens"], 0, 9, 3), "ones": _int(d["ones"], 0, 9, 5)}
    return {}


# ----- 그리기 -----
def _svg(w: float, h: float, body: str, label: str) -> Markup:
    return Markup(
        f'<svg class="dg" viewBox="0 0 {w:.0f} {h:.0f}" xmlns="http://www.w3.org/2000/svg" role="img" '
        f'aria-label="{escape(label)}">{body}</svg>'
    )


def _text(x, y, s, size=14, anchor="middle", weight=400) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" '
            f'fill="{INK}">{escape(str(s))}</text>')


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


EMOJI_FONT = "'Noto Emoji', 'Segoe UI Emoji', 'Apple Color Emoji', sans-serif"


def _emoji(x, y, e, size) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="middle" dominant-baseline="central" '
            f'style="font-family: {EMOJI_FONT}" fill="{INK}">{escape(e)}</text>')


def _item(kind: str, cx: float, cy: float, r: float = 6) -> str:
    if kind not in ("circle", "square", "star", "triangle"):
        return _emoji(cx, cy + 1, kind, r * 2.4)
    if kind == "square":
        return f'<rect x="{cx - r:.1f}" y="{cy - r:.1f}" width="{2 * r:.1f}" height="{2 * r:.1f}" fill="{INK}"/>'
    if kind == "triangle":
        return f'<polygon points="{cx:.1f},{cy - r:.1f} {cx + r:.1f},{cy + r:.1f} {cx - r:.1f},{cy + r:.1f}" fill="{INK}"/>'
    if kind == "star":
        pts = []
        for i in range(10):
            rr = r * 1.2 if i % 2 == 0 else r * 0.5
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
        return f'<polygon points="{" ".join(pts)}" fill="{INK}"/>'
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{INK}"/>'


def _draw_groups(s: dict) -> Markup:
    g, p = s["groups"], s["per_group"]
    inner_cols = math.ceil(math.sqrt(p))
    inner_rows = math.ceil(p / inner_cols)
    gap, pad = 18, 10
    bw, bh = inner_cols * gap + pad * 2 - 4, inner_rows * gap + pad * 2 - 4
    per_row = max(1, min(g, int(620 // (bw + 14))))
    rows = math.ceil(g / per_row)
    label_h = 18 if s["group_label"] else 0
    W = per_row * (bw + 14) + 10
    H = rows * (bh + 14 + label_h) + 10
    out = []
    for i in range(g):
        r, c = divmod(i, per_row)
        x0, y0 = 10 + c * (bw + 14), 10 + r * (bh + 14 + label_h)
        out.append(f'<rect x="{x0}" y="{y0}" width="{bw}" height="{bh}" rx="8" fill="none" stroke="{INK}" stroke-width="1.5"/>')
        for k in range(p):
            rr, cc = divmod(k, inner_cols)
            out.append(_item(s["item"], x0 + pad + 7 + cc * gap, y0 + pad + 7 + rr * gap))
        if s["group_label"]:
            out.append(_text(x0 + bw / 2, y0 + bh + 15, s["group_label"], 12))
    return _svg(W, H, "".join(out), f"{p}개씩 {g}묶음")


def _draw_array(s: dict) -> Markup:
    r, c, gap = s["rows"], s["cols"], 24
    W, H = c * gap + 20, r * gap + 20
    dots = "".join(_item("circle", 22 + j * gap, 22 + i * gap, 7) for i in range(r) for j in range(c))
    return _svg(W, H, dots, f"{r}줄 {c}칸 배열")


def _draw_number_line(s: dict) -> Markup:
    a, b, step = s["start"], s["end"], s["step"]
    W, x0, x1, y = 640, 30, 610, 90
    X = lambda v: x0 + (v - a) * (x1 - x0) / (b - a)  # noqa: E731
    out = [f'<line x1="{x0 - 15}" y1="{y}" x2="{x1 + 15}" y2="{y}" stroke="{INK}" stroke-width="2"/>',
           f'<polygon points="{x1 + 22},{y} {x1 + 12},{y - 5} {x1 + 12},{y + 5}" fill="{INK}"/>']
    unit = 1 if b - a <= 40 else (step if step < (b - a) / 5 else max(1, (b - a) // 20))
    v = a
    while v <= b:
        big = (v - a) % step == 0
        out.append(f'<line x1="{X(v):.1f}" y1="{y - (9 if big else 5)}" x2="{X(v):.1f}" y2="{y + (9 if big else 5)}" '
                   f'stroke="{INK}" stroke-width="{1.6 if big else 1}"/>')
        if big:
            if v in s["hidden"]:
                out.append(f'<rect x="{X(v) - 10:.1f}" y="{y + 14}" width="20" height="20" fill="none" stroke="{INK}"/>')
            else:
                out.append(_text(X(v), y + 30, _fmt(v), 13))
        v += unit
    for h in s["hidden"]:
        if (h - a) % step:
            out.append(f'<rect x="{X(h) - 10:.1f}" y="{y + 14}" width="20" height="20" fill="none" stroke="{INK}"/>')
    js = s["jumps"]
    for p, q in zip(js, js[1:]):
        mx, top = (X(p) + X(q)) / 2, y - 10 - min(40, abs(X(q) - X(p)) * 0.35)
        out.append(f'<path d="M{X(p):.1f},{y - 4} Q{mx:.1f},{top:.1f} {X(q):.1f},{y - 4}" fill="none" stroke="{INK}" stroke-width="1.5"/>')
        out.append(f'<polygon points="{X(q):.1f},{y - 3} {X(q) - (5 if q > p else -5):.1f},{y - 12} {X(q) + (2 if q > p else -2):.1f},{y - 11}" fill="{INK}"/>')
        d = q - p
        out.append(_text(mx, top + 2, ("+" if d > 0 else "−") + _fmt(abs(d)), 12, weight=700))
    for m in s["marks"]:
        out.append(f'<circle cx="{X(m):.1f}" cy="{y}" r="5.5" fill="{INK}"/>')
    return _svg(W, 135, "".join(out), f"{a}부터 {b}까지 수직선")


def _draw_fraction(s: dict) -> Markup:
    d, n = s["denominator"], s["numerator"]
    wholes = max(1, math.ceil(n / d))
    out = []
    if s["style"] == "circle":
        R, gap = 60, 20
        W, H = wholes * (2 * R + gap) + gap, 2 * R + 30
        for w in range(wholes):
            cx, cy = gap + R + w * (2 * R + gap), R + 15
            for k in range(d):
                a0, a1 = -math.pi / 2 + 2 * math.pi * k / d, -math.pi / 2 + 2 * math.pi * (k + 1) / d
                filled = w * d + k < n
                p0 = (cx + R * math.cos(a0), cy + R * math.sin(a0))
                p1 = (cx + R * math.cos(a1), cy + R * math.sin(a1))
                large = 1 if a1 - a0 > math.pi else 0
                out.append(f'<path d="M{cx},{cy} L{p0[0]:.1f},{p0[1]:.1f} A{R},{R} 0 {large} 1 {p1[0]:.1f},{p1[1]:.1f} Z" '
                           f'fill="{SHADE if filled else "none"}" stroke="{INK}" stroke-width="1.5"/>')
    else:
        bw, bh, gap = 520, 44, 16
        W, H = bw + 20, wholes * (bh + gap) + 10
        for w in range(wholes):
            y0 = 10 + w * (bh + gap)
            for k in range(d):
                filled = w * d + k < n
                out.append(f'<rect x="{10 + k * bw / d:.1f}" y="{y0}" width="{bw / d:.1f}" height="{bh}" '
                           f'fill="{SHADE if filled else "none"}" stroke="{INK}" stroke-width="1.5"/>')
    return _svg(W, H, "".join(out), f"{d}분의 {n}")


def _draw_clock(s: dict) -> Markup:
    cx = cy = 110
    R = 95
    out = [f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{INK}" stroke-width="3"/>']
    for i in range(60):
        a = math.radians(i * 6 - 90)
        r0 = R - (10 if i % 5 == 0 else 5)
        out.append(f'<line x1="{cx + r0 * math.cos(a):.1f}" y1="{cy + r0 * math.sin(a):.1f}" '
                   f'x2="{cx + R * math.cos(a):.1f}" y2="{cy + R * math.sin(a):.1f}" stroke="{INK}" '
                   f'stroke-width="{2 if i % 5 == 0 else 1}"/>')
    for h in range(1, 13):
        a = math.radians(h * 30 - 90)
        out.append(_text(cx + (R - 24) * math.cos(a), cy + (R - 24) * math.sin(a) + 6, h, 17, weight=700))
    if s["show_hands"]:
        m, h = s["minute"], s["hour"] % 12 + s["minute"] / 60
        am, ah = math.radians(m * 6 - 90), math.radians(h * 30 - 90)
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + 52 * math.cos(ah):.1f}" y2="{cy + 52 * math.sin(ah):.1f}" '
                   f'stroke="{INK}" stroke-width="6" stroke-linecap="round"/>')
        out.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + 78 * math.cos(am):.1f}" y2="{cy + 78 * math.sin(am):.1f}" '
                   f'stroke="{INK}" stroke-width="3" stroke-linecap="round"/>')
    out.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{INK}"/>')
    label = f"{s['hour']}시 {s['minute']}분" if s["show_hands"] else "빈 시계"
    return _svg(220, 220, "".join(out), label)


def _nice_max(v: float) -> tuple[float, float]:
    """세로축 최댓값과 눈금 간격."""
    if v <= 0:
        return 5, 1
    raw = v / 5
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    return math.ceil(v / step) * step, step


def _draw_chart(s: dict, line: bool) -> Markup:
    labels, values = s["labels"], s["values"]
    top, step = _nice_max(max(values))
    W, H, L, B, T, Rr = 600, 300, 60, 250, 40, 20
    Y = lambda v: B - (v / top) * (B - T)  # noqa: E731
    out = []
    if s["title"]:
        out.append(_text(W / 2, 22, s["title"], 15, weight=700))
    v = 0.0
    while v <= top + 1e-9:
        out.append(f'<line x1="{L}" y1="{Y(v):.1f}" x2="{W - Rr}" y2="{Y(v):.1f}" stroke="#999" stroke-width="0.8"/>')
        out.append(_text(L - 8, Y(v) + 4, _fmt(round(v, 6)), 12, anchor="end"))
        v += step
    out.append(f'<line x1="{L}" y1="{T - 5}" x2="{L}" y2="{B}" stroke="{INK}" stroke-width="1.5"/>')
    out.append(f'<line x1="{L}" y1="{B}" x2="{W - Rr}" y2="{B}" stroke="{INK}" stroke-width="1.5"/>')
    if s["unit"]:
        out.append(_text(L - 8, T - 12, f"({s['unit']})", 12, anchor="end"))
    slot = (W - Rr - L) / len(values)
    pts = []
    for i, (lab, val) in enumerate(zip(labels, values)):
        cx = L + slot * (i + 0.5)
        out.append(_text(cx, B + 20, lab, 12))
        if line:
            pts.append((cx, Y(val)))
        else:
            bw = min(48, slot * 0.6)
            out.append(f'<rect x="{cx - bw / 2:.1f}" y="{Y(val):.1f}" width="{bw:.1f}" height="{B - Y(val):.1f}" '
                       f'fill="{SHADE}" stroke="{INK}" stroke-width="1.2"/>')
    if line:
        out.append(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{INK}" stroke-width="2"/>')
        out += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{INK}"/>' for x, y in pts]
    return _svg(W, H, "".join(out), s["title"] or ("꺾은선그래프" if line else "막대그래프"))


def _draw_shape(s: dict) -> Markup:
    sh = s["shape"]
    W, H = 360, 260
    pts: list[tuple[float, float]]
    if sh == "circle":
        cx, cy, R = 180, 130, 100
        out = [f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="none" stroke="{INK}" stroke-width="2"/>',
               f'<circle cx="{cx}" cy="{cy}" r="3.5" fill="{INK}"/>']
        if s["radius_label"]:
            out.append(f'<line x1="{cx}" y1="{cy}" x2="{cx + R}" y2="{cy}" stroke="{INK}" stroke-width="1.5"/>')
            out.append(_text(cx + R / 2, cy - 8, s["radius_label"], 13))
        return _svg(W, H, "".join(out), "원")
    table = {
        "triangle": [(70, 220), (290, 220), (120, 50)],
        "right_triangle": [(80, 220), (290, 220), (80, 50)],
        "equilateral": [(80, 225), (280, 225), (180, 52)],
        "isosceles": [(100, 225), (260, 225), (180, 35)],
        "square": [(90, 40), (270, 40), (270, 220), (90, 220)],
        "rectangle": [(50, 60), (310, 60), (310, 200), (50, 200)],
        "parallelogram": [(100, 60), (320, 60), (260, 200), (40, 200)],
        "trapezoid": [(120, 60), (240, 60), (310, 200), (50, 200)],
        "rhombus": [(180, 25), (300, 130), (180, 235), (60, 130)],
    }
    if sh == "polygon":
        n = s["sides"]
        pts = [(180 + 100 * math.cos(-math.pi / 2 + 2 * math.pi * k / n), 130 + 100 * math.sin(-math.pi / 2 + 2 * math.pi * k / n))
               for k in range(n)]
    else:
        pts = table[sh]
    out = [f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="none" stroke="{INK}" stroke-width="2"/>']
    if sh in ("right_triangle", "square", "rectangle"):
        corners = [0] if sh == "right_triangle" else range(4)
        for i in corners:
            (x, y), (xn, yn), (xp, yp) = pts[i], pts[(i + 1) % len(pts)], pts[i - 1]
            ux, uy = (xn - x) / math.dist((x, y), (xn, yn)) * 14, (yn - y) / math.dist((x, y), (xn, yn)) * 14
            vx, vy = (xp - x) / math.dist((x, y), (xp, yp)) * 14, (yp - y) / math.dist((x, y), (xp, yp)) * 14
            out.append(f'<polyline points="{x + ux:.1f},{y + uy:.1f} {x + ux + vx:.1f},{y + uy + vy:.1f} {x + vx:.1f},{y + vy:.1f}" '
                       f'fill="none" stroke="{INK}" stroke-width="1.2"/>')
    cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    for i, lab in enumerate(s["side_labels"][:len(pts)]):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % len(pts)]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dx, dy = mx - cx, my - cy
        d = math.hypot(dx, dy) or 1
        out.append(_text(mx + dx / d * 20, my + dy / d * 20 + 5, lab, 13))
    return _svg(W, H, "".join(out), sh)


def _draw_angle(s: dict) -> Markup:
    deg = s["degrees"]
    vx, vy, L = 170, 170, 140
    a = math.radians(deg)
    out = [f'<line x1="{vx}" y1="{vy}" x2="{vx + L}" y2="{vy}" stroke="{INK}" stroke-width="2"/>',
           f'<line x1="{vx}" y1="{vy}" x2="{vx + L * math.cos(a):.1f}" y2="{vy - L * math.sin(a):.1f}" stroke="{INK}" stroke-width="2"/>',
           f'<circle cx="{vx}" cy="{vy}" r="3.5" fill="{INK}"/>']
    r = 34
    large = 1 if deg > 180 else 0
    out.append(f'<path d="M{vx + r},{vy} A{r},{r} 0 {large} 0 {vx + r * math.cos(a):.1f},{vy - r * math.sin(a):.1f}" '
               f'fill="none" stroke="{INK}" stroke-width="1.5"/>')
    mid = a / 2
    tx, ty = vx + 58 * math.cos(mid), vy - 58 * math.sin(mid) + 5
    if s["show_value"]:
        out.append(_text(tx, ty, f"{deg}°", 15, weight=700))
    else:
        out.append(f'<rect x="{tx - 11:.1f}" y="{ty - 16:.1f}" width="22" height="22" fill="none" stroke="{INK}"/>')
        out.append(_text(tx + 16, ty, "°", 15))
    return _svg(340, 200 if deg <= 180 else 330, "".join(out), f"{deg}도 각")


def _draw_base_ten(s: dict) -> Markup:
    u = 9  # 작은 정육면체 한 칸
    out, x = [], 10
    for _ in range(s["hundreds"]):
        out.append(f'<rect x="{x}" y="10" width="{10 * u}" height="{10 * u}" fill="none" stroke="{INK}" stroke-width="1.5"/>')
        out += [f'<line x1="{x + k * u}" y1="10" x2="{x + k * u}" y2="{10 + 10 * u}" stroke="{INK}" stroke-width="0.6"/>' for k in range(1, 10)]
        out += [f'<line x1="{x}" y1="{10 + k * u}" x2="{x + 10 * u}" y2="{10 + k * u}" stroke="{INK}" stroke-width="0.6"/>' for k in range(1, 10)]
        x += 10 * u + 12
    for _ in range(s["tens"]):
        out.append(f'<rect x="{x}" y="10" width="{u}" height="{10 * u}" fill="none" stroke="{INK}" stroke-width="1.5"/>')
        out += [f'<line x1="{x}" y1="{10 + k * u}" x2="{x + u}" y2="{10 + k * u}" stroke="{INK}" stroke-width="0.6"/>' for k in range(1, 10)]
        x += u + 6
    if s["ones"]:
        x += 6
        for k in range(s["ones"]):
            r, c = divmod(k, 3)
            out.append(f'<rect x="{x + c * (u + 4)}" y="{10 + 10 * u - (r + 1) * (u + 4) + 4}" width="{u}" height="{u}" '
                       f'fill="none" stroke="{INK}" stroke-width="1.3"/>')
        x += 3 * (u + 4)
    W = max(x + 10, 120)
    return _svg(W, 10 * u + 20, "".join(out), f"백 {s['hundreds']}, 십 {s['tens']}, 일 {s['ones']}")


def _draw_scene(s: dict) -> Markup:
    cell, out, x = 110, [], 10
    W = 20 + cell * len(s["emojis"])
    for e, lab, n in zip(s["emojis"], s["labels"], s["counts"]):
        size = 64 if n == 1 else (34 if n <= 4 else 24)
        per_row = 1 if n == 1 else (2 if n <= 4 else 4)
        rows = math.ceil(n / per_row)
        for k in range(n):
            r, c = divmod(k, per_row)
            cx = x + cell / 2 + (c - (min(n, per_row) - 1) / 2) * size * 1.05
            cy = 60 + (r - (rows - 1) / 2) * size * 1.05
            out.append(_emoji(cx, cy, e, size))
        if lab:
            out.append(_text(x + cell / 2, 132, lab, 14 if len(lab) <= 7 else max(9, round(14 * 7 / len(lab))), weight=700))
        x += cell
    out.insert(0, f'<rect x="3" y="3" width="{W - 6}" height="142" rx="14" fill="none" stroke="{INK}" stroke-width="1.5"/>')
    return _svg(W, 148, "".join(out), " ".join(s["labels"]))


def _draw_pictograph(s: dict) -> Markup:
    big, small = s["big"], s["big"] // 10
    L, row_h, W = 90, 46, 600
    out = [f'<rect x="1" y="1" width="{W - 2}" height="{row_h * len(s["labels"]) + 40}" fill="none" stroke="{INK}" stroke-width="1.5"/>',
           f'<line x1="{L}" y1="1" x2="{L}" y2="{row_h * len(s["labels"]) + 1}" stroke="{INK}"/>']
    for i, (lab, v) in enumerate(zip(s["labels"], s["values"])):
        y = 1 + i * row_h
        if i:
            out.append(f'<line x1="1" y1="{y}" x2="{W - 1}" y2="{y}" stroke="{INK}" stroke-width="0.8"/>')
        out.append(_text(L / 2, y + row_h / 2 + 5, lab, 14, weight=700))
        x = L + 22
        for _ in range(v // big):
            out.append(_emoji(x, y + row_h / 2, s["emoji"], 30))
            x += 34
        for _ in range((v % big) // max(1, small)):
            out.append(_emoji(x, y + row_h / 2 + 4, s["emoji"], 16))
            x += 19
    y = row_h * len(s["labels"]) + 1
    out.append(f'<line x1="1" y1="{y}" x2="{W - 1}" y2="{y}" stroke="{INK}" stroke-width="0.8"/>')
    out.append(_emoji(W - 250, y + 20, s["emoji"], 26))
    out.append(_text(W - 230, y + 25, f"{big}{s['unit']}", 13, anchor="start"))
    out.append(_emoji(W - 150, y + 22, s["emoji"], 14))
    out.append(_text(W - 138, y + 25, f"{small}{s['unit']}", 13, anchor="start"))
    return _svg(W, y + 40, "".join(out), "그림그래프")


def render_svg(spec) -> Markup:
    """설계도 → SVG. 그릴 수 없으면 빈 문자열."""
    s = normalize(spec)
    if not s:
        return Markup("")
    kind = s["kind"]
    if kind in ("bar_chart", "line_chart"):
        return _draw_chart(s, line=kind == "line_chart")
    return globals()[f"_draw_{kind}"](s)
