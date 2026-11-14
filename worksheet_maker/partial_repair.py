"""검토 지적에 필요한 조각만 받아 원본에 적용한다. 적용 뒤 전체 검토는 별도로 한다."""
from __future__ import annotations

import copy
import json
from .schema import QUESTION_TYPES, response_schema


def _layout(mode):
    if mode == 'activity':
        from . import activity
        props = activity.response_schema()['properties']
        return props, {'question_edits': 'questions', 'material_edits': 'materials'}
    if mode == 'worksheet':
        return response_schema()['properties'], {'block_edits': 'blocks'}
    raise ValueError('지원하지 않는 수정 형식입니다.')


def schema(mode: str) -> dict:
    props, arrays = _layout(mode)
    header = {k: v for k, v in props.items() if k not in {*arrays.values(), 'has_interest'}}
    output = {'header': {'type': 'object', 'properties': header}}
    for edits, field in arrays.items():
        output[edits] = {'type': 'array', 'items': {
            'type': 'object', 'properties': {
                'at': {'type': 'integer', 'minimum': 0},
                'delete': {'type': 'integer', 'minimum': 0},
                'values': {'type': 'array', 'items': props[field]['items']},
            }, 'required': ['at', 'delete', 'values']}}
    return {'type': 'object', 'properties': output, 'required': list(output)}


def prompt(original: str, draft: dict, issues: list[dict], mode: str) -> str:
    _, arrays = _layout(mode)
    shown = {k: v for k, v in draft.items() if k not in arrays.values()}
    number = 1
    for field in arrays.values():
        rows = []
        for index, value in enumerate(draft.get(field, [])):
            row = {'at': index, 'value': value}
            if field == 'questions':
                row['question_numbers'] = [index + 1]
            elif field == 'blocks' and value.get('type') in QUESTION_TYPES:
                count = len(value.get('items', [])) if value['type'] == 'drill' else 1
                row['question_numbers'] = list(range(number, number + count))
                number += count
            rows.append(row)
        shown[field] = rows
    return original + f"""

## 내용 검토 후 부분 수정 — 이번 응답의 출력 형식
위의 수업 조건·학년·성취기준·유형·문항 수는 유지한다. 이번 응답은 전체 학습지 대신 수정 조각 JSON이다.
지적된 문제와 그에 연결된 자료·조건·질문·선택지·정답·해설을 함께 고친다. 문제 없는 조각은 다시 출력하지 않는다.
원본에는 프로그램이 붙인 at(0부터 시작하는 배열 위치)와 question_numbers(인쇄 문항 번호)가 있다. at에는 문항 번호가 아니라 배열 위치를 쓴다.
수정 배열의 이름과 원본 배열: {json.dumps(arrays, ensure_ascii=False)}
각 수정은 {{"at": 원본 배열 위치, "delete": 원본에서 뺄 조각 수, "values": 넣을 완전한 조각 목록}}이다.
한 조각 교체는 delete=1, 삽입은 delete=0, 삭제만 할 때는 values=[]다. 모든 위치는 수정 전 원본 기준이며 범위가 겹치면 안 된다.
예: at=2의 조각 하나를 고치면 at=2, delete=1, values=[고친 조각]이다. 빠진 풀이 예시는 필요한 위치에 새 조각으로 삽입한다.
header는 제목·목표 등 머리말 중 실제로 고칠 필드만 넣고 없으면 {{}}다. 수정할 것이 없는 수정 배열은 []다.
새 조각의 본문·보기·정답·해설은 빠짐없이 쓴다. 기존 내용의 요약이나 '나머지는 그대로' 같은 글을 조각으로 쓰지 않는다.
수정 때문에 정답이 노출되거나 학년 범위를 벗어나지 않는지 다시 풀어 확인한다. 완성 후 프로그램이 전체 학습지를 재검토한다.

배열 위치를 표시한 원본:
{json.dumps(shown, ensure_ascii=False)}

검토 근거와 수정 사항:
{json.dumps(issues, ensure_ascii=False)}
"""


def apply(draft: dict, patch: dict, mode: str) -> dict:
    """모든 위치를 검사한 뒤 깊은 사본에 적용한다. 원본과 입력 조각은 바꾸지 않는다."""
    props, arrays = _layout(mode)
    expected = {'header', *arrays}
    if not isinstance(patch, dict) or set(patch) != expected or not isinstance(patch['header'], dict):
        raise ValueError('부분 수정의 필수 항목이 맞지 않습니다.')
    allowed = set(props) - {*arrays.values(), 'has_interest'}
    if set(patch['header']) - allowed:
        raise ValueError('수정할 수 없는 머리말 항목입니다.')
    result = copy.deepcopy(draft)
    result.update(copy.deepcopy(patch['header']))
    for edits, field in arrays.items():
        base, changes = draft.get(field, []), patch[edits]
        if not isinstance(base, list) or not isinstance(changes, list):
            raise ValueError('부분 수정 목록이 올바르지 않습니다.')
        ordered = []
        for change in changes:
            if not isinstance(change, dict) or set(change) != {'at', 'delete', 'values'}:
                raise ValueError('부분 수정 조각이 올바르지 않습니다.')
            at, remove, values = change['at'], change['delete'], change['values']
            if (type(at) is not int or type(remove) is not int or at < 0 or remove < 0
                    or at > len(base) or at + remove > len(base) or not isinstance(values, list)
                    or any(not isinstance(v, dict) for v in values)):
                raise ValueError('부분 수정 위치나 새 조각이 올바르지 않습니다.')
            ordered.append((at, remove, values))
        ordered.sort(key=lambda row: row[0])
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] == previous[0] or current[0] < previous[0] + previous[1]:
                raise ValueError('부분 수정 위치가 겹칩니다.')
        replaced = copy.deepcopy(base)
        for at, remove, values in reversed(ordered):
            replaced[at:at + remove] = copy.deepcopy(values)
        result[field] = replaced
    return result
