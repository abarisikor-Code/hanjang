"""정답 검산: AI가 만든 계산 문제의 정답을 프로그램이 다시 계산해 바로잡는다.

확실한 경우만 고친다 — 식이 분명하고(숫자와 + − × ÷ 괄호뿐) 정답이 수 하나일 때.
글로 된 문제(문장제)는 여기서 고치지 않고 AI 검토(llm.review_worksheet)에 맡긴다.
약분은 5학년부터 배우므로 reduce=False(4학년까지)면 식의 분모 그대로 둔다 (1/6 + 3/6 → 4/6).
"""

from __future__ import annotations

import re
from fractions import Fraction

from .schema import QUESTION_TYPES, Worksheet

_EXPR_CHARS = set("0123456789 ./+-×÷−–*()")
_NUM = r"\d+(?:\.\d+)?(?:\s+\d+/\d+|/\d+)?"  # 정수·소수·분수·대분수
_ONE_EXPR = re.compile(rf"(?<![\d./]){_NUM}(?:\s*[+\-×÷−]\s*\(?\s*{_NUM}\s*\)?)+")
_MIXED = r"\d+\s+\d+/\d+"


def calc(expr: str) -> Fraction | None:
    """'3/4 × 8/9', '457 + 368', '1 2/3 − 1/3' 같은 사칙연산 식의 정확한 값. 식이 아니면 None."""
    e = expr.replace("×", "*").replace("−", "-").replace("–", "-").strip()
    if not e or re.search(r"[^\d\s+\-*/().÷]", e) or re.search(r"\.(?!\d)|(?<!\d)\.|\*\*|//", e):
        return None
    # 분수는 한 덩어리: '3/4 ÷ 1/8'이 3/4/1/8로 읽히지 않게 (3/4)÷(1/8)로 묶은 뒤 ÷를 바꾼다
    e = re.sub(r"(\d+)\s+(\d+)\s*/\s*(\d+)", r"(\1+(\2/\3))", e)  # 대분수 1 2/3 → (1+(2/3))
    e = re.sub(r"(?<![\d(])(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", r"(\1/\2)", e)
    e = e.replace("÷", "/")
    e = re.sub(r"\d+(?:\.\d+)?", lambda m: f"F('{m.group(0)}')", e)  # 모든 수를 정확한 분수로 (소수 포함)
    try:
        value = eval(e, {"__builtins__": {}, "F": Fraction})  # 숫자·괄호·사칙연산·F만 남은 식
    except Exception:  # 0으로 나누기, 잘못된 식 등
        return None
    return value if isinstance(value, Fraction) else None


def number(text: str) -> Fraction | None:
    """수 하나('825', '3/5', '1 2/5', '0.75')의 값."""
    text = text.strip()
    if not re.fullmatch(_NUM, text):
        return None
    return calc(text + " + 0")


def fmt(value: Fraction, like: str = "", expr: str = "", reduce: bool = True) -> str:
    """값을 AI가 쓴 모양(정수·분수·대분수·소수)에 맞춰 쓴다. 식에 대분수가 있으면 대분수로.
    reduce=False면 약분하지 않고 식에 쓰인 분모 그대로 쓴다."""
    if value.denominator == 1:
        return str(value.numerator)
    if "." in like or ("." in expr and "/" not in expr):
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    den = value.denominator
    dens = {int(d) for d in re.findall(r"\d+/(\d+)", expr)}
    if not reduce and len(dens) == 1 and (value * next(iter(dens))).denominator == 1:
        den = dens.pop()
    num = int(value * den)
    whole, rest = divmod(num, den)
    if whole and (re.fullmatch(rf"\s*{_MIXED}\s*", like) or re.search(_MIXED, expr)):
        return f"{whole} {rest}/{den}"
    return f"{num}/{den}"


def _expr_before(text: str) -> str:
    """'… 457 + 368 =' 에서 '=' 바로 앞의 식 부분만 떼어 낸다."""
    i = len(text)
    while i > 0 and text[i - 1] in _EXPR_CHARS:
        i -= 1
    return text[i:].strip()


def fix_equations(text: str, reduce: bool) -> tuple[str, list[tuple[str, str]]]:
    """글 속 '식 = [[답]]'과 '나눗셈 = 몫 [[ ]], 나머지 [[ ]]'의 정답을 바로잡는다. (고친 글, [(원래 답, 고친 답)])"""
    changes: list[tuple[str, str]] = []

    def quotient(m: re.Match) -> str:
        q, r = divmod(int(m.group(1)), int(m.group(2)))
        new = f"{m.group(1)} ÷ {m.group(2)} = 몫 [[{q}]], 나머지 [[{r}]]"
        if (m.group(3), m.group(4)) != (str(q), str(r)):
            changes.append((f"몫 {m.group(3)}, 나머지 {m.group(4)}", f"몫 {q}, 나머지 {r}"))
        return new

    text = re.sub(r"(\d+)\s*÷\s*(\d+)\s*=\s*몫\s*\[\[\s*(\d+)\s*\]\]\s*,?\s*나머지\s*\[\[\s*(\d+)\s*\]\]", quotient, text)

    def proportion(m: re.Match) -> str:  # 비례식 a : b = c : d 에서 빈칸 하나 — 외항의 곱 = 내항의 곱
        terms = [m.group(k) for k in range(1, 5)]
        blanks = [k for k, t in enumerate(terms) if t.startswith("[[")]
        known = [number(t) for t in terms if not t.startswith("[[")]
        if len(blanks) != 1 or None in known:
            return m.group(0)
        k = blanks[0]
        given = terms[k][2:-2].strip()
        v = [number(t) if not t.startswith("[[") else None for t in terms]
        other = {0: 3, 1: 2, 2: 1, 3: 0}[k]  # 빈칸과 같은 쪽(외항/내항)의 짝
        a, b = [v[i] for i in ((1, 2) if k in (0, 3) else (0, 3))]
        if not v[other]:
            return m.group(0)
        right_v = a * b / v[other]
        if number(given) == right_v:
            return m.group(0)
        right = fmt(right_v, given, " ".join(t for t in terms if not t.startswith("[[")))
        changes.append((given, right))
        return m.group(0).replace(terms[k], f"[[{right}]]")

    term = rf"({_NUM}|\[\[[^\[\]]+\]\])"
    text = re.sub(rf"(?<![\d.]){term}\s*:\s*{term}\s*=\s*{term}\s*:\s*{term}(?![\d.])", proportion, text)

    def answer(m: re.Match) -> str:
        expr = _expr_before(text[:m.start()])
        before = text[:m.start()][:len(text[:m.start()].rstrip()) - len(expr)].rstrip()
        # 비례식 '4 : 3 = [[12]] : 9'처럼 '=' 양쪽이 식이 아닌 경우, 연산 없는 수 하나('3 = ')는 검산하지 않는다
        if before.endswith(":") or text[m.end():].lstrip().startswith(":") or not re.search(r"\d\s*[+\-×÷−–*/]\s*\(?\d", expr):
            return m.group(0)
        given = re.sub(r"\s+", " ", m.group(1).strip())
        value, got = calc(expr), number(given)
        if value is None or got is None:
            return m.group(0)
        right = fmt(value, given, expr, reduce)
        dens = set(re.findall(r"\d+/(\d+)", expr))
        reduced_early = (not reduce and len(dens) == 1 and "/" in given  # 4학년까지: 미리 약분한 답(2/3)을 4/6으로
                         and re.findall(r"/(\d+)", given)[-1] not in dens)
        if value != got or (reduce and "/" in given and given != right) or reduced_early:
            changes.append((given, right))
            return m.group(0).replace(m.group(1), right)
        return m.group(0)

    text = re.sub(r"=\s*\[\[([^\[\]]+)\]\]", answer, text)
    return text, changes


def _single_expr(body: str) -> str | None:
    """문항 글에 계산식이 딱 하나 있고, 그 뒤에 '='(이미 결과가 있음)가 없으면 그 식."""
    if "□" in body or "[[" in body or "(  )" in body:
        return None
    found = list(_ONE_EXPR.finditer(body))
    if len(found) != 1 or re.match(r"\s*\)?\s*=", body[found[0].end():]):
        return None
    expr = found[0].group(0).strip()
    while expr.count(")") > expr.count("(") and expr.endswith(")"):  # '(12 × 3)'의 짝 없는 괄호
        expr = expr[:-1].strip()
    return expr


def check(ws: Worksheet, reduce: bool) -> list[str]:
    """학습지의 계산 문제 정답을 검산해 바로잡는다. 고친 내용을 사람이 읽을 문장으로 돌려준다."""
    from .render import CIRCLED, _option_text

    notes: list[str] = []
    num = 0
    for b in ws.blocks:
        if b.type in QUESTION_TYPES:
            num += 1
        if b.type == "drill":
            items = []
            for k, it in enumerate(b.items, 1):
                new, changes = fix_equations(it, reduce)
                notes += [f"{num}번 ({k}) 정답 {a} → {c}" for a, c in changes]
                items.append(new)
            b.items = items
        elif b.type == "fill_blank":
            b.body, changes = fix_equations(b.body, reduce)
            notes += [f"{num}번 정답 {a} → {c}" for a, c in changes]
        elif b.type == "short_answer" and b.answer:
            expr = _single_expr(b.body)
            m = re.fullmatch(rf"\s*({_NUM})\s*([가-힣a-zA-Z²³%°]*)\s*", b.answer)  # 수 하나(+단위)인 답만
            value = calc(expr) if expr else None
            if value is not None and m and number(m.group(1)) is not None:
                right = fmt(value, m.group(1), expr, reduce)
                if number(m.group(1)) != value:
                    notes.append(f"{num}번 정답 {m.group(1)} → {right}")
                    b.solution = re.sub(rf"(=\s*){re.escape(m.group(1))}(?!\d)", rf"\g<1>{right}", b.solution)
                    b.answer = right + m.group(2)
        elif b.type == "multiple_choice" and b.items:
            expr = _single_expr(b.body)
            value = calc(expr) if expr else None
            options = [number(_option_text(o).split("=")[0]) if "=" not in _option_text(o) else None for o in b.items]
            if value is not None and all(o is not None for o in options):
                hits = [i for i, o in enumerate(options) if o == value]
                cur = CIRCLED.index(b.answer.strip()[0]) if b.answer.strip()[:1] in CIRCLED else -1
                if len(hits) == 1 and hits[0] != cur:
                    i = hits[0]
                    notes.append(f"{num}번 정답 → {CIRCLED[i]}")
                    b.answer = f"{CIRCLED[i]} {_option_text(b.items[i])}"
                elif not hits:
                    notes.append(f"{num}번: 보기에 정답({fmt(value, '', expr, reduce)})이 없음 — 확인이 필요해요")
    return notes + check_number_words(ws)


# ----- 문항 모양 검사 (AI 검토가 놓치기 쉬운 것을 프로그램이 확실하게 잡는다) -----
_NUM_PREFIX = re.compile(r"^\s*(?:\[?\d{1,2}\s*번\s*(?:문항|문제)?\]?[.:)]?|\d{1,2}[.)](?=\s)"
                         r"|(?:문항|문제|질문)\s*\d{1,2}\s*[.:)]?|(?:문항|질문)(?=\s))\s*")
_UNBLANK = re.compile(r"\[\[(.*?)\]\]")
_OX_MARK = re.compile(r"\s*(?:[(（]\s*(?:\[\[)?\s*[OXox○×]\s*(?:\]\])?\s*[)）]|\[\[\s*[OXox○×]\s*\]\]|(?<=[.!?다요])\s+[OX○×])\s*$")  # 진술문 끝에 붙은 정답 '(O)', '[[X]]'
# 문항이 가리키는 자료 → 그 자료가 될 수 있는 구성요소
_SOURCES = [
    (re.compile(r"(?:위|아래|다음)(?:의)?\s*(?:대화(?!\s*(?:태도|예절|방법|할\s*때))|글(?!자|씨)|이야기|시(?!계|각|간)|지문|편지|기사|일기)"),  # '위 시계'는 시(詩)가 아니다
     {"reading", "text", "quote"}),
    (re.compile(r"(?:위|아래|다음)(?:의)?\s*표"), {"table"}),
    (re.compile(r"(?:위|아래|다음)(?:의)?\s*(?:그림|수직선|시계|그래프|수 모형|도형|모형|지도)|(?:그림|수직선|그래프)을 보고"),
     {"diagram", "image"}),
]


def strip_numbers(ws: Worksheet) -> None:
    """AI가 문항 앞에 쓴 번호('1번 문항', '문제 3', '3.')를 지운다 — 번호는 render가 붙인다.
    O/X 진술문 끝에 붙은 정답('(O)')을 지우고, 정답 없는 안내 문장('~살펴보세요')은 문항이 아닌 설명 글로 바꾼다."""
    for b in ws.blocks:
        if b.type in QUESTION_TYPES:
            b.body = _NUM_PREFIX.sub("", b.body, count=1)
            b.title = _NUM_PREFIX.sub("", b.title, count=1)
        if b.type == "ox":  # '(1)' 같은 번호와 끝에 붙은 정답을 지운다 (정답 칸이 비었으면 그 표시를 정답으로 옮긴다)
            marks = [_OX_MARK.search(it) for it in b.items]
            if not b.answer.strip() and b.items and all(marks):
                b.answer = " ".join(f"({i}) {'O' if m.group(0).strip(' ()（）[]').upper() in ('O', '○') else 'X'}"
                                    for i, m in enumerate(marks, 1))
            b.items = [_OX_MARK.sub("", re.sub(r"^\s*\(?\d{1,2}\)\s*", "", it)) for it in b.items]
            sol_marks = re.findall(r"\((\d{1,2})\)\s*[:：]?\s*([OX])", b.solution)
            if not b.answer.strip() and b.items and len(sol_marks) == len(b.items):  # 정답이 해설에만 '(1) O: …'로 적힌 경우
                b.answer = " ".join(f"({n}) {m}" for n, m in sol_marks)
        if b.type in ("multiple_choice", "short_answer", "essay", "ox"):  # 강조하려고 쓴 [[ ]]는 굵게 (정답 자리인 빈칸만 남긴다)
            b.body = _UNBLANK.sub(lambda m: m.group(0) if _is_blank(m.group(1), b.answer) else f"**{m.group(1).strip()}**", b.body)
        if b.type in ("concept", "text", "reading", "section", "table", "activity"):  # 문항이 아닌 곳의 [[ ]]는 글자만 남긴다
            b.title, b.body = _UNBLANK.sub(r"\1", b.title), _UNBLANK.sub(r"\1", b.body)
            b.items = [_UNBLANK.sub(r"\1", it) for it in b.items]
            b.rows = [[_UNBLANK.sub(r"\1", c) for c in r] for r in b.rows]
        if (b.type == "short_answer" and not b.answer.strip() and not b.solution.strip()
                and not re.search(r"인가요|쓰세요|써 보세요|적어|구하|고르|답하|무엇|몇|어떤|어떻게|누구|어디|언제|왜", b.body)):
            # 정답도 해설도 없고 묻는 말도 없는 안내 문장('숲속 이야기로 들어가 볼까요?')은 문항이 아니다
            b.type = "text"


def _is_blank(inner: str, answer: str) -> bool:
    """문항 글의 [[inner]]가 진짜 빈칸인가: 비었거나 '□'·'___'이거나 정답에 들어 있는 말이면 빈칸이다.
    '다음 중 [[큰 소리]]를 내는 방법'처럼 정답과 상관없는 말은 AI가 강조하려고 쓴 것이다."""
    t = inner.strip()
    return not t or bool(re.fullmatch(r"[□_?\s]+|O|X", t)) or re.sub(r"\s+", "", t) in re.sub(r"\s+", "", answer)


def drop_empty_tables(ws: Worksheet) -> None:
    """제목만 있고 내용(머리줄 아래 줄)이 없는 표를 뺀다. 그 표를 보라던 문항은 missing_sources가 찾는다."""
    ws.blocks = [b for b in ws.blocks if not (b.type == "table" and len([r for r in b.rows if any(c.strip() for c in r)]) < 2)]


def missing_sources(ws: Worksheet) -> list[tuple[int, str]]:
    """'위 대화에서…'처럼 자료를 가리키는데 그 앞에 그런 자료가 없는 문항. [(문항 번호, 설명)]"""
    found, num, seen = [], 0, set()
    for b in ws.blocks:
        if b.type in QUESTION_TYPES:
            num += 1
            text = f"{b.title} {b.body}"
            for pattern, kinds in _SOURCES:
                m = pattern.search(text)
                if m and not (seen & kinds) and not ("\n" in b.body.strip() and kinds & {"reading", "text", "quote"}):
                    found.append((num, f"'{m.group(0)}'를 가리키는데 학습지에 그 자료가 없어요"))
                    break
        seen.add(b.type)
    return found


def answer_shown(ws: Worksheet) -> list[tuple[int, str]]:
    """영어 글자·낱말을 묻는데 그 답이 문제 속 영어 단어(예: 'apple의 첫 글자는?' → a)나 바로 위 그림 카드 이름표
    (예: 이름표 'cat' 아래 'c[[a]]t')에 보이는 문항. [(문항 번호, 설명)]"""
    from .render import CIRCLED

    found, num, shown = [], 0, set()
    for b in ws.blocks:
        if b.type == "diagram" and b.diagram:  # 그림 카드 이름표에 적힌 영어 낱말 (다음 그림이 나올 때까지 보인다)
            shown = {w.lower() for lab in b.diagram.get("labels") or [] for w in re.findall(r"[A-Za-z]{2,}", str(lab))}
        if b.type not in QUESTION_TYPES:
            continue
        num += 1
        if b.type == "ox":  # '맞으면 O'의 O는 정답이 드러난 것이 아니다
            continue
        # 정답 영어 낱말 (빈칸이면 빈칸을 채운 낱말: 'c[[a]]t' → cat)이 바로 위 그림 카드에 적혀 있으면 정답이 보인다
        if b.type == "fill_blank":
            words = ["".join(m.groups()) for m in re.finditer(r"([A-Za-z]*)\[\[([A-Za-z]{1,20})\]\]([A-Za-z]*)", b.body)]
        else:
            words = [re.sub(rf"^[{CIRCLED}]\s*", "", b.answer.strip())]
        hit = next((w for w in words if re.fullmatch(r"[A-Za-z]{2,20}", w) and w.lower() in shown), None)
        if hit:
            found.append((num, f"정답 '{hit}'가 바로 위 그림 카드 이름표에 그대로 적혀 있어요"))
            continue
        if b.type == "drill":  # 'apple → [[a]]pple'처럼 같은 줄에 그 낱말을 다 보여 주고 철자를 묻는 연습 문제
            for it in b.items:
                for m in re.finditer(r"([A-Za-z]*)\[\[([A-Za-z]{1,20})\]\]([A-Za-z]*)", it):
                    w, rest = "".join(m.groups()), it[:m.start()] + " " + it[m.end():]
                    if (m.group(1) or m.group(3)) and re.search(rf"(?<![A-Za-z]){re.escape(w)}(?![A-Za-z])", rest, re.I):
                        hit = w
            if hit:
                found.append((num, f"연습 문제에 '{hit}' 낱말을 다 보여 주고 철자를 물어요"))
            continue
        answers = re.findall(r"\[\[([A-Za-z]{1,3})\]\]", b.body) if b.type == "fill_blank" else [b.answer.strip()]
        body = re.sub(r"\[\[.*?\]\]", " ", b.body)
        flags = 0 if re.search(r"대문자|소문자", body) else re.I  # 'D의 소문자는?' → d: 대소문자 바꾸기는 정답이 드러난 게 아니다
        for a in answers:
            word = rf"(?<![A-Za-z]){re.escape(a)}[A-Za-z]+"  # 'apple'처럼 답으로 시작하는 영어 단어
            letter = rf"(?<![A-Za-z]){re.escape(a)}(?![A-Za-z])"  # '브(b) 소리'처럼 답 글자 그대로
            if re.fullmatch(r"[A-Za-z]{1,3}", a) and (re.search(word, body, flags) or re.search(letter, body, flags)):
                found.append((num, f"정답 '{a}'가 문제 속 영어 단어에 그대로 보여요"))
                break
    return found


def figure_issues(ws: Worksheet) -> list[tuple[int, str]]:
    """그림·표·보기가 문항과 맞지 않는 경우: 빈 표를 보고 풀라는 문항, 수 모형이 계산 문제의 정답을 보여 주거나 엉뚱한 수를
    보여 줌, 선택형 보기가 겹침."""
    from .render import _option_text

    found, num, last_fig, last_table = [], 0, None, None
    for b in ws.blocks:
        if b.type == "table":
            last_table = b
        if b.type == "diagram" and b.diagram:
            last_fig = b.diagram
        if b.type not in QUESTION_TYPES:
            continue
        num += 1
        text = f"{b.title} {b.body}"
        if b.type == "multiple_choice":
            opts = [re.sub(r"\s+", " ", _option_text(o)).strip() for o in b.items]  # 띄어쓰기 문제는 공백만 달라도 다른 보기
            if len(set(opts)) < len(opts):
                found.append((num, "선택형 보기에 같은 것이 두 번 있어요"))
        if "표" in text and last_table is not None and len(last_table.rows) < 2:
            found.append((num, "표를 보고 풀라는데 표가 비어 있어요"))
        fig, last_fig = last_fig, None  # 그림 바로 뒤 첫 문항만 본다
        if fig and fig.get("kind") == "base_ten" and re.search(r"\d\s*[+\-−]\s*\d", text):
            shown = fig.get("hundreds", 0) * 100 + fig.get("tens", 0) * 10 + fig.get("ones", 0)
            numbers = {int(x) for x in re.findall(r"\d+", text)}
            answer = number(re.sub(r"[^\d/.\s]", "", b.answer_text()).strip() or "x")
            if answer is not None and shown == answer:
                found.append((num, f"수 모형 그림이 정답({shown})을 그대로 보여 줘요"))
            elif shown not in numbers:
                found.append((num, f"수 모형 그림의 수({shown})가 문항의 수와 맞지 않아요"))
    return found


# ----- 수 읽기·쓰기 (예: 4050200 ↔ 사백오만 이백) -----
_DIGIT = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_SMALL = {"십": 10, "백": 100, "천": 1000}
_BIG = {"만": 10**4, "억": 10**8, "조": 10**12}
_KOREAN_NUM = re.compile(r"^[일이삼사오육칠팔구십백천만억조영\s]+$")


def korean_to_int(text: str) -> int | None:
    """'사백오만 이백' → 4050200. 수를 읽은 말이 아니면 None."""
    t = re.sub(r"\s+", "", text.strip())
    if not t or not _KOREAN_NUM.match(t):
        return None
    if t == "영":
        return 0
    total = section = digit = 0
    for ch in t:
        if ch in _DIGIT:
            if digit:
                return None  # '사오'처럼 숫자가 겹치면 수 읽기가 아니다
            digit = _DIGIT[ch]
        elif ch in _SMALL:
            section += (digit or 1) * _SMALL[ch]
            digit = 0
        elif ch in _BIG:
            total += (section + digit or 1) * _BIG[ch]
            section = digit = 0
        else:
            return None
    return total + section + digit


def int_to_korean(n: int) -> str:
    """4050200 → '사백오만 이백' (교과서처럼 만·억·조 단위로 띄어 쓴다)."""
    if n == 0:
        return "영"
    names = "일이삼사오육칠팔구"

    def four(x: int) -> str:
        out = ""
        for unit, word in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
            d = x // unit % 10
            if d:
                out += ("" if d == 1 and word else names[d - 1]) + word
        return out

    parts = []
    for unit, word in ((10**12, "조"), (10**8, "억"), (10**4, "만"), (1, "")):
        chunk = n // unit % 10000
        if chunk:
            parts.append(four(chunk) + word)
    return " ".join(parts)


def check_number_words(ws: Worksheet) -> list[str]:
    """수 읽기·쓰기 문항의 정답을 바로잡는다: '4050200을 읽어 보세요'(답: 한글), ''삼천이백십만'을 숫자로'(답: 숫자), 읽기 선택형."""
    from .render import CIRCLED, _option_text

    notes, num = [], 0
    for b in ws.blocks:
        if b.type not in QUESTION_TYPES:
            continue
        num += 1
        body = b.body
        numbers = [int(x.replace(",", "")) for x in re.findall(r"(?<![\d.])\d[\d,]{1,}(?![\d.])", body)]
        if b.type == "short_answer" and re.search(r"읽어|읽으면|읽는", body) and len(numbers) == 1:
            got = korean_to_int(b.answer)
            if got is not None and got != numbers[0]:
                right = int_to_korean(numbers[0])
                notes.append(f"{num}번 정답 {b.answer} → {right}")
                b.answer = right
        quoted = re.findall(r"['‘’\"“”]([일이삼사오육칠팔구십백천만억조영\s]+)['‘’\"“”]", body)
        if b.type == "short_answer" and "숫자로" in body and len(quoted) == 1 and re.fullmatch(r"\s*[\d,]+\s*", b.answer):
            want = korean_to_int(quoted[0])
            if want is not None and int(b.answer.replace(",", "")) != want:
                notes.append(f"{num}번 정답 {b.answer.strip()} → {want}")
                b.answer = str(want)
        if b.type == "multiple_choice" and re.search(r"읽은|읽기|읽는", body) and len(numbers) == 1:
            vals = [korean_to_int(_option_text(o)) for o in b.items]
            hits = [i for i, v in enumerate(vals) if v == numbers[0]]
            cur = CIRCLED.index(b.answer.strip()[0]) if b.answer.strip()[:1] in CIRCLED else -1
            if len(hits) == 1 and hits[0] != cur:
                notes.append(f"{num}번 정답 → {CIRCLED[hits[0]]}")
                b.answer = f"{CIRCLED[hits[0]]} {_option_text(b.items[hits[0]])}"
    return notes
