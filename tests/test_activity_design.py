"""활동 내용 보존, 잘못된 설계 거절, 기존 생성 경로 유지를 확인한다."""
import copy
import json
import unittest
from unittest.mock import patch

from worksheet_maker import activity, llm
from worksheet_maker.schema import Worksheet, Block, QUESTION_TYPES


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


def passing_generation(plan=None):
    def generate(client, model, prompt, files, schema):
        return json.dumps({'issues': []} if 'issues' in schema['properties'] else plan or sample_plan())
    return generate


class ActivityDesignTests(unittest.TestCase):
    def test_inquiry_questions_are_the_activity_not_a_detached_appendix(self):
        plan = sample_plan()
        plan['questions'][0]['stage'] = '대안 비교'
        plan['questions'][1]['stage'] = '결과 정리'
        ws = activity.to_worksheet(plan, 2, kind='inquiry')
        self.assertEqual(ws.kind, 'inquiry')
        self.assertEqual([b.title for b in ws.blocks if b.type == 'section'], ['대안 비교', '결과 정리'])
        self.assertEqual(sum(b.type in QUESTION_TYPES for b in ws.blocks), 2)
        self.assertEqual(ws.blocks[-1].type, 'reflection')

    def test_contextual_test_keeps_exam_layout(self):
        plan = sample_plan()
        plan['questions'][-1]['action'] = '이유 설명'
        ws = activity.to_worksheet(plan, 2, kind='test')
        self.assertEqual(ws.kind, 'test')
        self.assertEqual(ws.goals, [])
        self.assertFalse(any(b.type in {'text', 'section', 'activity', 'reflection'} for b in ws.blocks))
        self.assertEqual(sum(b.type == 'reading' for b in ws.blocks), 1)

    def test_ox_answer_combinations_are_replaced_with_the_actual_statements(self):
        plan = sample_plan()
        plan['questions'][0].update(type='ox', body='설명을 확인해 보세요.\n(1) 표의 단위를 확인합니다.\n(2) 조건은 읽지 않아도 됩니다.',
            items=['(1) O (2) X', '(1) X (2) O'], answer='(1) O (2) X')
        ws = llm._postprocess(activity.to_worksheet(plan, 2, kind='inquiry'), 4)
        block = next(b for b in ws.blocks if b.type == 'ox')
        self.assertEqual(block.items, ['표의 단위를 확인합니다.', '조건은 읽지 않아도 됩니다.'])
        self.assertEqual(block.answer, '(1) O (2) X')

    def test_fractional_won_is_rejected_but_subunits_are_allowed(self):
        for rows in [[['물품', '가격'], ['동화책', '3.50원']], [['물품', '가격(원)'], ['동화책', '3.50']]]:
            plan = sample_plan()
            plan['materials'][0]['rows'] = rows
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, '원 단위'):
                activity.to_worksheet(plan, 2)
        plan = sample_plan()
        plan['materials'][0]['rows'] = [['이름', '무게(kg)', '가격'], ['기부물품', '3.50', '3.50만원']]
        self.assertIsNotNone(activity.to_worksheet(plan, 2))
        for unit in ('천원', '천 원', '만 원'):
            plan['materials'][0]['rows'] = [['물품', f'가격({unit})'], ['동화책', '3.50']]
            with self.subTest(unit=unit):
                self.assertIsNotNone(activity.to_worksheet(plan, 2))

    def test_detached_calculation_appendix_is_flagged(self):
        ws = Worksheet(title='가상 장터', blocks=[
            Block(type='short_answer', body=f'[수학] {n}/8 + 1/8을 계산하면 얼마인가요?', answer='값', solution='풀이') for n in (1, 2, 3, 4)
        ])
        self.assertTrue(any('단순 계산' in s for s in activity.context_issues(ws)))

    def test_price_column_cannot_contain_a_fraction_of_food(self):
        ws = Worksheet(title='가상 장터', blocks=[Block(type='table', rows=[['물품', '가격'], ['간식', '2/6판']])])
        self.assertTrue(any('가격 열' in s for s in activity.context_issues(ws)))

    def test_repeated_formula_choice_then_calculation_pairs_are_flagged(self):
        questions = []
        for expression, answer in [('12 × 14', '168'), ('144 ÷ 12', '12')]:
            questions += [Block(type='multiple_choice', body='이 상황에 맞는 식을 골라 보세요.', answer='③ '+expression),
                          Block(type='short_answer', body=expression+'의 계산 결과로 알맞은 것을 골라 보세요.', answer=answer)]
        questions += [Block(type='essay', body='자료의 조건을 바탕으로 계획을 비교해 보세요.', answer='예시', solution='기준') for _ in range(6)]
        ws = Worksheet(title='가상 평가', blocks=questions)
        self.assertTrue(any('문항 쌍' in s for s in activity.context_issues(ws)))

    def test_test_and_inquiry_enter_design_path(self):
        for kind, request in [('inquiry', '호랑이를 좋아해'), ('inquiry', '')]:
            with self.subTest(kind=kind), patch.object(llm, '_generate', side_effect=passing_generation()) as design, patch.object(llm, '_generate_with_retry') as old:
                raw, _ = llm._generate_for_request(None, llm.MODELS[0], '원래 지시', request=request,
                    context='4학년 수학과 사회', n_questions=2, kind=kind)
            self.assertEqual(json.loads(raw)['kind'], kind)
            self.assertEqual(design.call_count, 2)
            old.assert_not_called()

    def test_integrated_design_does_not_require_a_separate_interest_string(self):
        with patch.object(llm, '_generate', side_effect=passing_generation()) as design:
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '원래 지시', request='',
                context='4학년 수학과 사회', n_questions=2, kind="inquiry", force_context=True)
        self.assertEqual(json.loads(raw)['title'], sample_plan()['title'])
        self.assertIn('has_interest=true', design.call_args_list[0].args[2])

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

    def test_passing_plan_is_reviewed_without_rewriting(self):
        with patch.object(llm, '_generate', side_effect=passing_generation()) as design, patch.object(llm, '_generate_with_retry') as old:
            raw, used = llm._generate_for_request(None, llm.MODELS[0], '기존 지시',
                request='공룡을 좋아해', context='3학년 곱셈', n_questions=2, kind="inquiry")
        self.assertEqual(used, 'gemini-3.8-flash')
        self.assertEqual(json.loads(raw)['blocks'][-2]['answer'], sample_plan()['questions'][-1]['answer'])
        self.assertEqual(design.call_count, 2)
        self.assertIn('issues', design.call_args_list[1].args[4]['properties'])
        old.assert_not_called()

    def test_basic_without_interest_also_enters_content_review(self):
        with patch.object(llm, '_generate_reviewed_worksheet', return_value=('검토된 결과', '검토 모델')) as review:
            result = llm._generate_for_request(None, llm.MODELS[0], '지시', request='', context='1학년', n_questions=4)
        self.assertEqual(result[0], '검토된 결과')
        review.assert_called_once()

    def test_selected_model_is_used_for_design(self):
        with patch.object(llm, '_generate', side_effect=passing_generation()) as design:
            _, used = llm._generate_for_request(None, 'gemini-3.6-flash', '기존 지시',
                request='공룡을 좋아해', context='3학년 곱셈', n_questions=2, kind="inquiry")
        self.assertEqual(used, 'gemini-3.6-flash')
        self.assertEqual(design.call_args.args[1], 'gemini-3.6-flash')

    def test_design_failure_does_not_silently_downgrade_to_lite(self):
        with patch.object(llm, '_generate', side_effect=llm._BrokenOutput('설계 실패')) as design, patch.object(
                llm, '_generate_with_retry') as original:
            with self.assertRaisesRegex(llm.AnalyzeError, '활동 설계를 마치지 못했어요'):
                llm._generate_for_request(None, llm.MODELS[0], '기존 지시', request='공룡을 좋아해',
                    context='3학년 곱셈', n_questions=2, kind="inquiry")
        self.assertEqual([call.args[1] for call in design.call_args_list], ['gemini-3.8-flash', 'gemini-3.6-flash'])
        original.assert_not_called()


if __name__ == '__main__':
    unittest.main()
