"""활동 내용 보존, 잘못된 설계 거절, 기존 생성 경로 유지를 확인한다."""
import copy
import json
import unittest
from unittest.mock import patch

from worksheet_maker import activity, llm


def sample_plan():
    return {
        'has_interest': True, 'role': '공룡 박물관 준비팀', 'purpose': '전시안을 골라 완성하기',
        'title': '나의 공룡 전시', 'subject': '수학', 'unit': '곱셈', 'goals': ['곱셈으로 전시 방법을 비교해요'],
        'introduction': '활동을 위해 만든 자료예요. 전시 방법을 골라 보세요.',
        'materials': [{'title': '전시 방법', 'body': '두 방법 모두 48장을 전시해요.',
            'rows': [['방법', '한 줄 카드 수', '줄 수'], ['가', '12', '4'], ['나', '16', '3']]}],
        'questions': [
            {'action': '비교와 선택', 'type': 'multiple_choice',
             'body': '세 줄만 쓸 수 있다면 어느 방법을 골라야 하나요?', 'items': ['가', '나'],
             'answer': '②', 'solution': '나 방법은 16×3=48이고 세 줄만 써요.'},
            {'action': '결과물 완성', 'type': 'essay',
             'body': '나 방법의 카드 수와 줄 수를 넣어 전시 안내문을 완성해 보세요.', 'items': [],
             'answer': '한 줄에 16장씩 3줄에 48장을 전시합니다.', 'solution': '16×3=48이므로 조건에 맞아요.'},
        ],
    }


class ActivityDesignTests(unittest.TestCase):
    def test_materials_questions_and_answers_survive_without_rewriting(self):
        plan = sample_plan()
        before = copy.deepcopy(plan)
        ws = activity.to_worksheet(plan, 2)
        self.assertEqual(plan, before)
        self.assertEqual(ws.blocks[1].body, plan['materials'][0]['body'])
        self.assertEqual(ws.blocks[2].rows, plan['materials'][0]['rows'])
        self.assertEqual(ws.blocks[-2].items, ['가', '나'])
        self.assertEqual(ws.blocks[-1].body, plan['questions'][-1]['body'])
        self.assertEqual(ws.blocks[-1].answer, plan['questions'][-1]['answer'])
        self.assertEqual(ws.blocks[-1].type, 'essay')

    def test_incomplete_and_shallow_plans_are_rejected(self):
        cases = []
        p = sample_plan(); p['questions'].pop(); cases.append(p)
        p = sample_plan(); p['questions'][0]['answer'] = ''; cases.append(p)
        p = sample_plan(); p['questions'][-1]['action'] = '자료 파악'; cases.append(p)
        p = sample_plan(); p['questions'][0]['answer'] = '③'; cases.append(p)
        p = sample_plan(); p['purpose'] = ''; cases.append(p)
        for plan in cases:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                activity.to_worksheet(plan, 2)

    def test_exact_choice_text_is_normalized_without_another_model_call(self):
        plan = sample_plan()
        plan['questions'][0]['items'] = ['① 가', '② 나']
        plan['questions'][0]['answer'] = '나'
        ws = activity.to_worksheet(plan, 2)
        self.assertEqual(ws.blocks[-2].items, ['가', '나'])
        self.assertEqual(ws.blocks[-2].answer, '②')

    def test_interest_plan_uses_one_model_call_and_keeps_result(self):
        with patch.object(llm, '_generate', return_value=json.dumps(sample_plan())) as design, patch.object(llm, '_generate_with_retry') as old:
            raw, used = llm._generate_for_request(None, llm.MODELS[0], '기존 지시',
                request='공룡을 좋아해', context='3학년 곱셈', n_questions=2)
        self.assertEqual(used, 'gemini-3.8-flash')
        self.assertEqual(json.loads(raw)['blocks'][-1]['answer'], sample_plan()['questions'][-1]['answer'])
        design.assert_called_once()
        old.assert_not_called()

    def test_non_interest_request_falls_back_to_original_generation(self):
        with patch.object(llm, '_generate', return_value=json.dumps({'has_interest': False})), patch.object(
                llm, '_generate_with_retry', return_value=('기존 결과', llm.MODELS[0])) as old:
            result = llm._generate_for_request(None, llm.MODELS[0], '원래 지시',
                request='쉽게', context='1학년 덧셈', n_questions=4)
        self.assertEqual(result[0], '기존 결과')
        old.assert_called_once()

    def test_selected_model_is_used_for_design(self):
        with patch.object(llm, '_generate', return_value=json.dumps(sample_plan())) as design:
            _, used = llm._generate_for_request(None, 'gemini-3.6-flash', '기존 지시',
                request='공룡을 좋아해', context='3학년 곱셈', n_questions=2)
        self.assertEqual(used, 'gemini-3.6-flash')
        self.assertEqual(design.call_args.args[1], 'gemini-3.6-flash')

    def test_design_failure_does_not_silently_downgrade_to_lite(self):
        with patch.object(llm, '_generate', side_effect=llm._BrokenOutput('설계 실패')) as design, patch.object(
                llm, '_generate_with_retry') as original:
            with self.assertRaisesRegex(llm.AnalyzeError, '활동 설계를 마치지 못했어요'):
                llm._generate_for_request(None, llm.MODELS[0], '기존 지시', request='공룡을 좋아해',
                    context='3학년 곱셈', n_questions=2)
        self.assertEqual([call.args[1] for call in design.call_args_list], ['gemini-3.8-flash', 'gemini-3.6-flash'])
        original.assert_not_called()


if __name__ == '__main__':
    unittest.main()
