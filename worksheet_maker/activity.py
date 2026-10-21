"""관심사·융합 활동 설계를 기본·평가·탐구 학습지 구성요소로 옮긴다."""
from __future__ import annotations

import re

from .schema import Block, Worksheet


def response_schema() -> dict:
    def obj(properties):
        return {"type": "object", "properties": properties, "required": list(properties)}

    text = {"type": "string"}
    texts = {"type": "array", "items": text}
    return obj({
        "has_interest": {"type": "boolean"},
        "role": text,
        "purpose": text,
        "title": text,
        "subject": text,
        "unit": text,
        "goals": texts,
        "introduction": text,
        "materials": {"type": "array", "items": obj({
            "title": text, "body": text,
            "rows": {"type": "array", "items": texts},
        })},
        "questions": {"type": "array", "items": obj({
            "action": {"type": "string", "enum": ["자료 파악", "비교와 선택", "조건에 맞게 결정", "결과물 완성", "이유 설명"]},
            "stage": {"type": "string", "enum": ["문제 파악과 예상", "자료 조사", "대안 비교", "해결안 결정", "결과 정리"]},
            "type": {"type": "string", "enum": ["short_answer", "essay", "multiple_choice", "ox"]},
            "body": text,
            "items": texts,
            "answer": text,
            "solution": text,
        })},
    })


def build_prompt(*, context: str, request: str, n_questions: int, kind: str = "standard",
                 force_context: bool = False) -> str:
    kind_rule = {
        "standard": "자료를 살펴보고 비교·선택한 뒤 결과물을 완성한다.",
        "test": "단원평가지다. 선택형 약 절반, 나머지는 단답형과 맨 끝 서술형 1~2개로 평가한다. 긴 역할극·개념 정리·별도 활동 안내는 넣지 않는다. 마지막은 자료에 근거한 해결안 판단이나 이유 설명이다.",
        "inquiry": "탐구·활동지다. 문제 파악과 예상 → 자료 조사 → 대안 비교 → 해결안 결정 → 결과 정리 순으로 각 문항의 stage를 정한다. 예상·조사·결론 자체가 요청한 문항 수 안에 들어간다. 결론 뒤에 별도의 확인 문제 묶음을 붙이지 않는다. 조사할 자료는 모두 제공하고, 없는 정보를 실제로 조사했다고 가정하지 않는다.",
    }.get(kind, "")
    return f"""너는 초등 수업을 설계하는 교사다. 학습지 꾸미기와 문제 유형 배합은 생각하지 말고,
아이가 관심사 속에서 배운 내용을 써서 의미 있는 일을 해내는 짧은 활동 하나를 설계한다. JSON으로 답한다.

## 지켜야 할 수업 조건
{context}

## 아이의 관심사와 요청
{request.strip() or '배울 내용에 맞는 생활 속 공동 문제를 정해 주세요.'}

## 유형과 문항 수
{kind_rule}
문항 수는 꼭 {n_questions}개다. 모든 문항이 같은 자료와 해결 목적에 쓰이게 한다.
{'과목융합 또는 탐구 요청이므로 관심사 표현이 없어도 has_interest=true로 하고 배울 내용에 맞는 상황을 설계한다.' if force_context else ''}

## 설계 순서
1. 요청에 좋아하는 대상이나 주제가 없고 '쉽게', '문제 수' 같은 조건만 있으면 has_interest=false로 한다.
   이때 나머지 글은 빈 문자열, 목록은 빈 목록으로 답한다. 관심사가 있으면 has_interest=true다.
2. 아이가 맡을 role, 수업 끝에 해결할 purpose를 정한다. 목적은 '계산하기·문제 풀기·공부하기'가 아니라
   '어떤 조건에 맞는 전시안을 고르기', '소식지 요약을 완성하기'처럼 결과가 있어야 한다.
3. 필요한 자료(materials)를 먼저 모두 준다. 지은 자료의 수·상황은 introduction에 '활동을 위해 만든 자료'라고 밝힌다.
   사실인 척 동물 먹이량·실제 경기 결과 등을 지어내지 않는다. body는 읽을 글, rows는 표(첫 행은 머리글)이며 없으면 비운다.
   표와 문항에서 같은 수의 의미·시점을 바꾸지 않는다. 표가 '오늘 들어온 수'라면 그 수를 '나눠 준 수'로 쓰지 않는다.
   저학년은 자료를 짧은 글 하나나 표 하나로 단순하게 하고, 마지막에는 준비표·이름표 등 작은 결과물을 완성한다.
   계산 연습을 위해 수량·단위를 억지로 만들지 않는다. 한국 원화 가격은 정수 원으로 쓴다(동화책 3.50원, 색연필 2.80원 금지).
   소수 학습은 물품의 무게 kg, 물의 양 L, 통로 길이 m처럼 소수가 자연스러운 양으로 연결한다. 가격 열에 분수로 나타낸 음식 양을 섞지 않는다.
   만 원·천 원 단위를 쓰면 그 단위를 표와 문항에 분명히 쓴다. 납득 가능한 규모의 수를 고른다.
   동물의 하루 먹이량 같은 전문 사실을 계산에 맞춰 지어내지 않는다. 사육사 주제는 운반 상자·보관 공간·준비물·물통의 조건을 활용한다.
4. questions는 꼭 {n_questions}개다. 각 문제에는 action, type, 실제 질문(body), 선택지나 O/X 진술(items), 정답, 해설을 모두 쓴다.
   type은 short_answer(짧은 답), essay(설명·완성), multiple_choice(선택형), ox 중 내용에 알맞은 것만 쓴다. 골고루 섞을 의무는 없다.
   최소 절반은 조건에 따른 비교·선택·결과물 완성 또는 자료를 근거로 판단 이유를 설명하는 문항이다. '알맞은 계산 결과 고르기'만으로는 비교·선택이 아니다.
   '120상자에 4개씩이면 모두 몇 개인가'는 자료 파악이지 결정이 아니다. action 이름만 결정으로 붙이지 않는다.
   비교할 안은 같은 필요를 해결하는 서로 다른 방법이어야 한다. 남는 물건을 줄이려고 돌볼 동물 수나 필요한 쉼터 수를 임의로 바꾸지 않는다.
   예: 돌볼 호랑이와 필요한 준비물 수는 고정하고, 포장 단위가 다른 두 구입안을 비교해 부족하지 않고 남는 수가 적은 안을 고른다.
   예: 같은 수의 카드를 배치하는 두 안을 계산으로 비교한 뒤 공간 조건에 맞는 안을 고른다.
   예: 중심 생각을 보존한 두 요약을 비교해 고른 뒤 소식지에 실을 최종 요약을 쓴다.
   마지막 문제는 앞에서 준비한 내용을 써서 purpose를 실제로 달성한다. 기본·탐구형의 마지막 action은 '결과물 완성'이고,
   평가지 마지막 action은 조건에 맞는 결정·비교·완성·이유 설명 중 하나다. 개념 O/X로 끝내지 않는다.
   같은 식을 먼저 고르게 하고 바로 다음 문항에서 그 식을 계산하게 하는 쌍을 반복하지 않는다. 독립적인 맨 식 계산은 전체의 1/5 이하로 한다.
5. 활동은 한 가지 배울 내용 안에서 이어지게 한다. 문항을 복잡하게 만들거나 학년 밖 개념으로 넓히지 않는다.
   저학년·기초는 한 문항에 짧은 행동 하나만 요구하고, 선택도 두 가지 중에서 고르는 정도로 한다.
   앞 문제의 오답이 뒤 문제로 번지지 않게 자료·조건은 다시 확인할 수 있게 둔다. 다른 문항의 정답은 알려주지 않는다.
   앞에서 구하게 한 합계·나머지·선택안을 뒤 문제 본문이나 선택지의 괄호에 그대로 적지 않는다. 뒤 문제에는 원자료만 다시 주거나 새로운 조건을 더한다.
6. 인터넷 검색, 별도 준비물, 실제 방문 없이 이 자료만으로 풀 수 있어야 한다. 문장은 '~해 보세요'로 쓴다.
   번호는 쓰지 않는다. body에 정답이나 [[정답]] 표시는 넣지 않는다. 정답은 answer에만 넣는다.
   items가 있으면 선택형(정답은 ①~⑤)이나 O/X(정답은 '(1) O (2) X')로 쓴다. 선택·설계에 여러 답이 가능하면 예시 답과 판단 조건을 쓴다.
7. 정답과 해설을 직접 다시 풀어 확인한다. 수의 범위·단위·자료와 해설이 일치해야 한다.
   과목이 여럿이면 고른 과목 모두 실제로 쓰되 과목별 나누기 조건이 있으면 지킨다.
   수학·사회 융합은 수학 계산 결과가 주민의 불편·의견·공정성 등의 사회 문제 해결안을 정하는 근거가 되게 한다.
   공동 자료의 의견·조건을 실제 문항에서 인용해 대안을 비교하고 결정한다. 계산 문제 뒤에 '회의란 무엇인가' 같은 사회 용어 문제를 따로 붙이지 않는다.

출력 전에 검사한다: 관심사 이름만 다른 계산 반복인가? 마지막 문제를 풀면 목적을 실제로 달성하는가?
앞의 조건을 못 지켰으면 스스로 고친 설계만 제출한다. HTML·CSS·그림 설계·장황한 개념 정리는 만들지 않는다.
"""


def to_worksheet(plan: dict, n_questions: int, kind: str = "standard") -> Worksheet | None:
    """설계 내용을 다시 쓰는 AI 호출 없이 학습지 구성요소로 옮긴다."""
    from .render import CIRCLED, _option_text

    if not isinstance(plan.get("has_interest"), bool):
        raise ValueError("관심사 여부가 올바르지 않습니다.")
    if not plan["has_interest"]:
        return None
    questions = plan.get("questions", [])
    if len(questions) != n_questions:
        raise ValueError("활동 설계의 문제 수가 요청과 다릅니다.")
    decisions = {"비교와 선택", "조건에 맞게 결정", "결과물 완성", "이유 설명"}
    if sum(q.get("action") in decisions for q in questions) < (n_questions + 1) // 2:
        raise ValueError("비교·선택·완성 활동이 부족합니다.")
    final_actions = {"결과물 완성"} if kind != "test" else decisions | {"이유 설명"}
    if not questions or questions[-1].get("action") not in final_actions:
        raise ValueError("마지막 문제에서 활동을 완성하지 못했습니다.")
    if not all(str(plan.get(k, "")).strip() for k in ("role", "purpose", "title", "introduction")):
        raise ValueError("활동의 역할·목적·안내가 비어 있습니다.")
    blocks = [] if kind == "test" else [Block(type="text", body=plan["introduction"])]
    reading_parts = [plan["introduction"]] if kind == "test" else []
    for material in plan.get("materials", []):
        if material.get("body", "").strip():
            if kind == "test":
                reading_parts.append(material["body"])
            else:
                blocks.append(Block(type="reading", title=material.get("title", ""),
                                    body=material["body"], source="학습용 창작 글"))
        if material.get("rows"):
            blocks.append(Block(type="table", title=material.get("title", ""), rows=material["rows"]))
    if reading_parts:
        blocks.insert(0, Block(type="reading", body="\n\n".join(reading_parts), source="학습용 창작 글"))
    stage = None
    for question in questions:
        if not all(str(question.get(k, "")).strip() for k in ("body", "answer", "solution")):
            raise ValueError("질문·정답·해설 중 비어 있는 칸이 있습니다.")
        items = question.get("items", [])
        answer = question["answer"]
        question_kind = question.get("type")
        if question_kind not in {"short_answer", "essay", "multiple_choice", "ox"}:
            raise ValueError("활동의 문항 형식이 올바르지 않습니다.")
        if question_kind == "multiple_choice":
            items = [_option_text(item) for item in items]
            if answer.strip() in items:
                answer = CIRCLED[items.index(answer.strip())]
            if not 2 <= len(items) <= 5 or answer.strip()[:1] not in CIRCLED[:len(items)]:
                raise ValueError("선택지와 정답이 맞지 않습니다.")
        if question_kind == "ox" and not items:
            raise ValueError("O/X 진술문이 없습니다.")
        if question_kind == "ox" and all(re.fullmatch(r"[\s()0-9.OXox]+", item) for item in items):
            parts = re.split(r"\(\d{1,2}\)\s*", question["body"])
            if len(parts) < 3 or not all(part.strip() for part in parts[1:]):
                raise ValueError("O/X 선택지에 진술문 대신 정답 조합만 있습니다.")
            body = parts[0].strip()
            items = [part.strip() for part in parts[1:]]
        else:
            body = question["body"]
        if kind == "inquiry" and question.get("stage", "자료 조사") != stage:
            stage = question.get("stage", "자료 조사")
            blocks.append(Block(type="section", title=stage))
        blocks.append(Block(type=question_kind, body=body, items=items, answer=answer,
                            solution=question["solution"], lines=4 if question_kind == "essay" else 1 if question_kind == "short_answer" else 0))
    if kind == "inquiry":
        blocks.append(Block(type="reflection", items=["자료를 근거로 문제를 살펴봤어요.", "여러 해결 방법을 비교했어요.", "선택한 해결안의 이유를 설명할 수 있어요."]))
    ws = Worksheet(title=plan["title"], subject=plan.get("subject", ""), unit=plan.get("unit", ""), kind=kind,
                   goals=[] if kind == "test" else plan.get("goals", []), blocks=blocks)
    issues = context_issues(ws)
    if issues:
        raise ValueError(" / ".join(issues))
    return ws


def context_issues(ws: Worksheet) -> list[str]:
    """명백한 단위 오류와 공동 자료에서 떨어진 계산 문항의 편중을 찾는다."""
    from .schema import QUESTION_TYPES

    texts = []
    issues = []
    for block in ws.blocks:
        texts.extend([block.body, *block.items])
        texts.extend(cell for row in block.rows for cell in row)
        if block.rows:
            for col, heading in enumerate(block.rows[0]):
                if re.search(r"가격|금액|비용", heading):
                    cells = [row[col] for row in block.rows[1:] if col < len(row)]
                    if not re.search(r"만\s*원|천\s*원", heading) and any(re.fullmatch(r"\s*\d+\.\d+\s*", cell) for cell in cells):
                        issues.append("원 단위 가격에 소수를 썼습니다.")
                    if any(re.search(r"\d+\s*/\s*\d+\s*(?:판|개|kg|L|m)", cell) for cell in cells):
                        issues.append("가격 열에 금액 대신 물품의 양을 썼습니다.")
    if any(re.search(r"(?<![\d.])\d+(?:,\d{3})*\.\d+\s*원", text) for text in texts):
        issues.append("원 단위 가격에 소수를 썼습니다.")
    questions = [b for b in ws.blocks if b.type in QUESTION_TYPES]
    generic = 0
    repeated_pairs = 0
    phrases = ("분모가 같은 분수의", "소수 두 자리 수", "덧셈", "뺄셈", "의 계산 결과로 알맞은 것을 골라 보세요", "의 몫을 골라 보세요",
               "를 계산한 값은 얼마인가요", "를 계산하면 얼마인가요", "을 계산하면 얼마인가요", "를 더한 값은 얼마인가요", "와", "과")
    for index, block in enumerate(questions):
        text = re.sub(r"\[[^]]+\]", "", block.body)
        for phrase in phrases:
            text = text.replace(phrase, "")
        text = re.sub(r"\d+(?:\.\d+)?|[\s/×÷+−=().,?\-]", "", text)
        generic += not text
        if not text and index:
            expressions = re.findall(r"\d+(?:\.\d+)?\s*[×÷+−-]\s*\d+(?:\.\d+)?", questions[index-1].answer)
            body = re.sub(r"\s+", "", block.body)
            repeated_pairs += any(re.sub(r"\s+", "", expression) in body for expression in expressions)
    if generic > max(1, len(questions) // 5):
        issues.append("공동 자료와 연결되지 않은 단순 계산 문항이 너무 많습니다.")
    if repeated_pairs >= 2:
        issues.append("같은 식을 고르고 바로 계산하는 문항 쌍을 반복했습니다.")
    return list(dict.fromkeys(issues))
