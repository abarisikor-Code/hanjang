"""기존 흐름 검사의 가상 수정 응답. 위치 보존은 별도 부분 수정 검사에서 확인한다."""
def worksheet_patch(ws):
    data = ws.model_dump()
    return {'header': {k: data[k] for k in ('title', 'subject', 'unit', 'lesson', 'goals')},
            'block_edits': [{'at': 0, 'delete': len(data['blocks']), 'values': data['blocks']}]}


def activity_patch(plan):
    return {'header': {k: v for k, v in plan.items() if k not in ('has_interest', 'questions', 'materials')},
            'question_edits': [{'at': 0, 'delete': 2, 'values': plan['questions']}],
            'material_edits': [{'at': 0, 'delete': 1, 'values': plan['materials']}]}
