"""학습지 유형: 모양(테마)과 따로, 학습지의 짜임새와 배치를 정한다.

- AI에게는 유형별 '짜임새 규칙'(RULES)만 준다. 2단 편집·격자·빙고판 같은 배치는 render·CSS(`wt-<유형>` 클래스)가 만든다.
- SAMPLES는 유형 고르기 화면의 미리보기용 예시 학습지.
"""

from __future__ import annotations

# 유형 → (아이콘, 이름, 사용자에게 보여 줄 설명)
KINDS: dict[str, tuple[str, str, str]] = {
    "standard": ("📘", "기본 학습지", "개념 정리 → 문제 → 스스로 점검. 한 차시를 고르게 담아요."),
    "test": ("📝", "단원평가지", "2단 시험지. 점수 칸과 문항별 배점이 붙고, 선택형·단답형 위주로 실력을 확인해요."),
    "drill": ("🧮", "연습 문제지", "짧은 문제를 칸에 촘촘히 담아 반복 연습해요. 연산·낱말·맞춤법 연습에 좋아요."),
    "note": ("🗂", "개념 정리 노트", "왼쪽 핵심어, 오른쪽 빈칸 설명의 코넬 노트. 배운 내용을 스스로 정리해요."),
    "inquiry": ("🔍", "탐구·활동지", "질문 → 예상 → 관찰·기록표 → 결론. 큰 활동 칸과 기록표가 중심이에요."),
    "game": ("🎲", "놀이 학습지", "문제를 풀고 빙고판에서 정답을 찾아 색칠해요. 저학년·복습에 좋아요."),
}

# AI에게 주는 짜임새 규칙. {n}은 문항 수.
RULES: dict[str, str] = {
    "standard": "- 순서: section(소제목) → concept(핵심 개념 정리) 또는 짧은 text/reading → 문항들 → 마지막에 reflection(스스로 점검 3개).",
    "test": (
        "- 단원평가 시험지다. section·concept·text·activity·reflection·goals는 쓰지 않는다"
        "(문제를 푸는 데 꼭 필요한 읽기 자료만 reading 1개까지, 도식은 문항에 필요한 것만).\n"
        "- 문항만 꼭 {n}개: 선택형 약 절반, 단답형·빈칸 채우기 약 1/3, 서술형 1~2개는 맨 뒤. 쉬운 문항 → 어려운 문항 순서.\n"
        "- 배점과 점수 칸은 프로그램이 붙인다. 문항에 점수를 쓰지 않는다."
    ),
    "drill": (
        "- 반복 연습지다. drill(연습 문제 묶음) {blocks}개가 중심이고, 묶음마다 문제 {per}개씩(모두 합해 {n2}개). 개수를 꼭 지킨다.\n"
        "- drill마다 title에 지시문(예: '계산해 보세요', '알맞은 낱말을 써 보세요'), items에 짧은 문제를 꼭 8개 이상(8~16개). "
        "각 문제의 정답 자리는 [[정답]]으로 표시한다(예: '37 + 48 = [[85]]', '(되/돼)요 → [[돼]]요').\n"
        "- drill마다 쉬운 문제 → 어려운 문제 순서. 같은 문제를 되풀이하지 않는다. 맨 앞에 풀이 요령을 concept 1개로 짧게 둘 수 있다.\n"
        "- 계산 정답은 하나하나 다시 계산해 확인한다.\n"
        "- 마지막에 생활 속 문제 short_answer 1~2개. section·reading·activity·reflection은 쓰지 않는다."
    ),
    "note": (
        "- 개념 정리 노트(코넬식)다. cornell(정리 노트) 2~4개가 중심: title에 소주제, items는 '핵심어: 설명' 형식으로 3~6개.\n"
        "  설명 속 꼭 알아야 할 낱말·수를 [[정답]] 빈칸으로 만든다(항목마다 1~2개).\n"
        "- cornell 사이에 이해를 돕는 diagram이나 table을 1~2개 둘 수 있다.\n"
        "- 그 뒤에 확인 문항 3~4개(단답형·선택형), 맨 끝에 essay 1개: body '오늘 배운 내용을 한두 문장으로 정리해 보세요.', lines 3.\n"
        "- section은 맨 앞에 하나만. reading·activity·reflection은 쓰지 않는다."
    ),
    "inquiry": (
        "- 탐구·활동지다. 순서: section(탐구 질문을 제목으로) → text(상황, 2~3문장) → activity(title '예상하기': 무엇이 될지 먼저 생각) → "
        "activity(title '조사·관찰하기', items에 순서 3~5개) → table(기록표: 첫 행은 머리글, 나머지 3~5행은 칸을 빈 문자열로) → "
        "drawing(관찰한 것·정리한 것을 그림이나 도식으로) → essay(알게 된 점·결론, lines 4) → reflection.\n"
        "- 문항은 4~6개로 적게 하고 생각을 쓰는 칸을 넉넉히 둔다. 수학이면 '조사해서 표·그래프로 정리하기', "
        "국어면 '자료 찾기 → 정리 → 발표 준비'처럼 그 과목다운 활동으로."
    ),
    "game": (
        "- 놀이 학습지(빙고)다. 맨 앞에 text 1개로 놀이 방법을 쓴다: '문제를 풀고, 빙고판에서 정답을 찾아 색칠해요. "
        "가로·세로·대각선 한 줄을 완성하면 빙고!'\n"
        "- 문항은 short_answer 또는 fill_blank로 {n}개(8개 이하). 정답은 한두 낱말이나 수로 짧게 쓰고, 단위는 빼고 수만 쓴다.\n"
        "- 정답이 문제 글에 보이면 안 된다(예: 'apple의 첫 글자는?'은 답 a가 보이므로 쓰지 않는다 — 한국어 뜻이나 그림 카드로 묻는다). "
        "문항끼리 정답이 겹치지 않게 한다(빙고판에 같은 칸이 생기지 않게).\n"
        "- 맨 끝에 bingo 1개: title '정답 빙고', items에 정답과 헷갈리는 오답(정답이 아닌 그럴듯한 값) 8개 이상. "
        "빙고판은 프로그램이 정답과 오답을 섞어 만든다.\n"
        "- section·concept·reading·reflection은 쓰지 않는다. 저학년도 즐겁게 풀 수 있는 말투로."
    ),
}


def rule(kind: str, n_questions: int) -> str:
    total = max(16, n_questions * 2)  # 연습 문제지: 문항 수의 두 배를 짧은 문제로
    blocks = max(2, -(-total // 10))  # 묶음 하나에 10개 안팎
    per = -(-total // blocks)
    return RULES.get(kind, RULES["standard"]).format(n=n_questions, n2=blocks * per, blocks=blocks, per=per)


def label(kind: str) -> str:
    icon, name, _ = KINDS.get(kind, KINDS["standard"])
    return f"{icon} {name}"


# ----- 유형 고르기 미리보기용 예시 (3학년 수학) -----
_COMMON = {"subject": "수학", "unit": "1. 덧셈과 뺄셈", "lesson": "3차시"}
SAMPLES: dict[str, dict] = {
    "standard": {**_COMMON, "title": "받아올림이 있는 덧셈", "goals": ["받아올림이 있는 세 자리 수의 덧셈을 할 수 있다."], "blocks": [
        {"type": "section", "title": "개념 익히기"},
        {"type": "concept", "items": ["받아올림: 같은 자리 수의 합이 10이 넘으면 윗자리로 1을 올려요."]},
        {"type": "section", "title": "문제 풀기"},
        {"type": "short_answer", "body": "457 + 368을 계산해 보세요."},
        {"type": "multiple_choice", "body": "계산 결과가 가장 큰 것은?", "items": ["245 + 318", "199 + 402", "376 + 125"]},
        {"type": "reflection", "items": ["받아올림을 바르게 표시했어요.", "자리를 맞추어 계산했어요."]},
    ]},
    "test": {**_COMMON, "title": "1단원 단원평가", "blocks": [
        {"type": "multiple_choice", "body": "245 + 318의 값은?", "items": ["553", "563", "573", "583"]},
        {"type": "multiple_choice", "body": "계산이 바른 것은?", "items": ["126 + 35 = 151", "208 + 94 = 302", "315 + 89 = 394"]},
        {"type": "short_answer", "body": "684 − 235를 계산해 보세요."},
        {"type": "fill_blank", "body": "500 − 127 = [[373]]"},
        {"type": "multiple_choice", "body": "어림한 값이 600에 가장 가까운 것은?", "items": ["298 + 315", "412 + 245", "150 + 389"]},
        {"type": "short_answer", "body": "사탕이 325개, 젤리가 142개 있어요. 모두 몇 개인가요?"},
        {"type": "essay", "body": "456 + 278을 계산하는 방법을 설명해 보세요.", "lines": 3},
    ]},
    "drill": {**_COMMON, "title": "덧셈 빨리 풀기", "blocks": [
        {"type": "drill", "title": "계산해 보세요.", "items": [
            "125 + 38 = [[163]]", "247 + 56 = [[303]]", "318 + 94 = [[412]]", "409 + 87 = [[496]]",
            "256 + 167 = [[423]]", "378 + 245 = [[623]]", "189 + 476 = [[665]]", "567 + 288 = [[855]]"]},
        {"type": "drill", "title": "빈칸에 알맞은 수를 써 보세요.", "items": [
            "[[300]] + 245 = 545", "128 + [[72]] = 200", "[[150]] − 75 = 75", "600 − [[250]] = 350",
            "[[408]] + 92 = 500", "999 − [[111]] = 888"]},
    ]},
    "note": {**_COMMON, "title": "덧셈과 뺄셈 정리 노트", "blocks": [
        {"type": "section", "title": "핵심 정리"},
        {"type": "cornell", "title": "받아올림이 있는 덧셈", "items": [
            "자리 맞추기: 같은 [[자리]]끼리 줄을 맞추어 써요.", "받아올림: 합이 [[10]]이 넘으면 윗자리로 1을 올려요.",
            "어림하기: 몇백으로 [[어림]]해서 답을 확인해요."]},
        {"type": "cornell", "title": "받아내림이 있는 뺄셈", "items": [
            "받아내림: 뺄 수 없으면 윗자리에서 [[10]]을 빌려와요.", "확인: 차 + 빼는 수 = [[빼어지는 수]]"]},
        {"type": "essay", "body": "오늘 배운 내용을 한두 문장으로 정리해 보세요.", "lines": 2},
    ]},
    "inquiry": {**_COMMON, "title": "우리 반 저금통 조사", "blocks": [
        {"type": "section", "title": "탐구 질문: 우리 모둠은 일주일에 얼마를 모을까?"},
        {"type": "activity", "title": "예상하기", "body": "모둠 친구들이 모을 돈을 어림해 보세요."},
        {"type": "activity", "title": "조사하기", "items": ["요일별로 모은 돈을 적어요.", "모두 더해요."]},
        {"type": "table", "title": "기록표", "rows": [["요일", "모은 돈(원)", "누적(원)"], ["월", "", ""], ["화", "", ""], ["수", "", ""]]},
        {"type": "essay", "body": "조사해서 알게 된 점을 써 보세요.", "lines": 2},
    ]},
    "game": {**_COMMON, "title": "덧셈 빙고 놀이", "blocks": [
        {"type": "text", "body": "문제를 풀고 빙고판에서 정답을 찾아 색칠해요. 한 줄을 완성하면 빙고!"},
        {"type": "short_answer", "body": "125 + 38", "answer": "163"},
        {"type": "short_answer", "body": "247 + 56", "answer": "303"},
        {"type": "short_answer", "body": "318 + 94", "answer": "412"},
        {"type": "short_answer", "body": "409 + 87", "answer": "496"},
        {"type": "bingo", "title": "정답 빙고", "items": ["153", "313", "402", "486", "500", "263", "422", "396"]},
    ]},
}
