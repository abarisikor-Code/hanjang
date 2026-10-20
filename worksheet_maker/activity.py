"""관심사가 있는 기본 학습지를 활동 설계에서 기존 구성요소로 옮긴다."""
from __future__ import annotations

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
            "type": {"type": "string", "enum": ["short_answer", "essay", "multiple_choice", "ox"]},
            "body": text,
            "items": texts,
            "answer": text,
            "solution": text,
        })},
    })


def build_prompt(*, context: str, request: str, n_questions: int) -> str:
    return f"""너는 초등 수업을 설계하는 교사다. 학습지 꾸미기와 문제 유형 배합은 생각하지 말고,
아이가 관심사 속에서 배운 내용을 써서 의미 있는 일을 해내는 짧은 활동 하나를 설계한다. JSON으로 답한다.

## 지켜야 할 수업 조건
{context}

## 아이의 관심사와 요청
{request.strip()}

## 설계 순서
1. 요청에 좋아하는 대상이나 주제가 없고 '쉽게', '문제 수' 같은 조건만 있으면 has_interest=false로 한다.
   이때 나머지 글은 빈 문자열, 목록은 빈 목록으로 답한다. 관심사가 있으면 has_interest=true다.
2. 아이가 맡을 role, 수업 끝에 해결할 purpose를 정한다. 목적은 '계산하기·문제 풀기·공부하기'가 아니라
   '어떤 조건에 맞는 전시안을 고르기', '소식지 요약을 완성하기'처럼 결과가 있어야 한다.
3. 필요한 자료(materials)를 먼저 모두 준다. 지은 자료의 수·상황은 introduction에 '활동을 위해 만든 자료'라고 밝힌다.
   사실인 척 동물 먹이량·실제 경기 결과 등을 지어내지 않는다. body는 읽을 글, rows는 표(첫 행은 머리글)이며 없으면 비운다.
   표와 문항에서 같은 수의 의미·시점을 바꾸지 않는다. 표가 '오늘 들어온 수'라면 그 수를 '나눠 준 수'로 쓰지 않는다.
   저학년은 자료를 짧은 글 하나나 표 하나로 단순하게 하고, 마지막에는 준비표·이름표 등 작은 결과물을 완성한다.
4. questions는 꼭 {n_questions}개다. 각 문제에는 action, type, 실제 질문(body), 선택지나 O/X 진술(items), 정답, 해설을 모두 쓴다.
   type은 short_answer(짧은 답), essay(설명·완성), multiple_choice(선택형), ox 중 내용에 알맞은 것만 쓴다. 골고루 섞을 의무는 없다.
   최소 절반은 조건에 따른 비교·선택·결과물 완성이다. '알맞은 계산 결과 고르기'만으로는 비교·선택이 아니다.
   예: 같은 수의 카드를 배치하는 두 안을 계산으로 비교한 뒤 공간 조건에 맞는 안을 고른다.
   예: 중심 생각을 보존한 두 요약을 비교해 고른 뒤 소식지에 실을 최종 요약을 쓴다.
   마지막 문제는 앞에서 준비한 내용을 써서 purpose를 실제로 달성해야 하고 action은 '결과물 완성'이다. 개념 O/X로 끝내지 않는다.
5. 활동은 한 가지 배울 내용 안에서 이어지게 한다. 문항을 복잡하게 만들거나 학년 밖 개념으로 넓히지 않는다.
   저학년·기초는 한 문항에 짧은 행동 하나만 요구하고, 선택도 두 가지 중에서 고르는 정도로 한다.
   앞 문제의 오답이 뒤 문제로 번지지 않게 자료·조건은 다시 확인할 수 있게 둔다. 다른 문항의 정답은 알려주지 않는다.
6. 인터넷 검색, 별도 준비물, 실제 방문 없이 이 자료만으로 풀 수 있어야 한다. 문장은 '~해 보세요'로 쓴다.
   번호는 쓰지 않는다. body에 정답이나 [[정답]] 표시는 넣지 않는다. 정답은 answer에만 넣는다.
   items가 있으면 선택형(정답은 ①~⑤)이나 O/X(정답은 '(1) O (2) X')로 쓴다. 선택·설계에 여러 답이 가능하면 예시 답과 판단 조건을 쓴다.
7. 정답과 해설을 직접 다시 풀어 확인한다. 수의 범위·단위·자료와 해설이 일치해야 한다.
   과목이 여럿이면 고른 과목 모두 실제로 쓰되 과목별 나누기 조건이 있으면 지킨다.

출력 전에 검사한다: 관심사 이름만 다른 계산 반복인가? 마지막 문제를 풀면 목적을 실제로 달성하는가?
앞의 조건을 못 지켰으면 스스로 고친 설계만 제출한다. HTML·CSS·그림 설계·장황한 개념 정리는 만들지 않는다.
"""


def to_worksheet(plan: dict, n_questions: int) -> Worksheet | None:
    """설계 내용을 다시 쓰는 AI 호출 없이 학습지 구성요소로 옮긴다."""
    from .render import CIRCLED, _option_text

    if not isinstance(plan.get("has_interest"), bool):
        raise ValueError("관심사 여부가 올바르지 않습니다.")
    if not plan["has_interest"]:
        return None
    questions = plan.get("questions", [])
    if len(questions) != n_questions:
        raise ValueError("활동 설계의 문제 수가 요청과 다릅니다.")
    decisions = {"비교와 선택", "조건에 맞게 결정", "결과물 완성"}
    if sum(q.get("action") in decisions for q in questions) < (n_questions + 1) // 2:
        raise ValueError("비교·선택·완성 활동이 부족합니다.")
    if not questions or questions[-1].get("action") != "결과물 완성":
        raise ValueError("마지막 문제에서 활동을 완성하지 못했습니다.")
    if not all(str(plan.get(k, "")).strip() for k in ("role", "purpose", "title", "introduction")):
        raise ValueError("활동의 역할·목적·안내가 비어 있습니다.")
    blocks = [Block(type="text", body=plan["introduction"])]
    for material in plan.get("materials", []):
        if material.get("body", "").strip():
            blocks.append(Block(type="reading", title=material.get("title", ""),
                                body=material["body"], source="학습용 창작 글"))
        if material.get("rows"):
            blocks.append(Block(type="table", title=material.get("title", ""), rows=material["rows"]))
    for question in questions:
        if not all(str(question.get(k, "")).strip() for k in ("body", "answer", "solution")):
            raise ValueError("질문·정답·해설 중 비어 있는 칸이 있습니다.")
        items = question.get("items", [])
        answer = question["answer"]
        kind = question.get("type")
        if kind not in {"short_answer", "essay", "multiple_choice", "ox"}:
            raise ValueError("활동의 문항 형식이 올바르지 않습니다.")
        if kind == "multiple_choice":
            items = [_option_text(item) for item in items]
            if answer.strip() in items:
                answer = CIRCLED[items.index(answer.strip())]
            if not 2 <= len(items) <= 5 or answer.strip()[:1] not in CIRCLED[:len(items)]:
                raise ValueError("선택지와 정답이 맞지 않습니다.")
        if kind == "ox" and not items:
            raise ValueError("O/X 진술문이 없습니다.")
        blocks.append(Block(type=kind, body=question["body"], items=items, answer=answer,
                            solution=question["solution"], lines=3 if kind == "essay" else 1 if kind == "short_answer" else 0))
    return Worksheet(title=plan["title"], subject=plan.get("subject", ""), unit=plan.get("unit", ""),
                     goals=plan.get("goals", []), blocks=blocks)
