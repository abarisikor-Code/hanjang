"""내용 검토를 건너뛰거나, 고치지 못한 초안을 사용자에게 내보내지 않는지 확인한다."""
import copy
import json
import unittest
from unittest.mock import patch
import httpx

from worksheet_maker import activity, llm
from worksheet_maker.schema import Block, Worksheet
from test_activity_design import sample_plan


def issue():
    return {'category': 'realism', 'question': 0,
            'evidence': '가족 네 명의 한 끼에 달걀 250개를 준비한다.',
            'fix': '식사 규모에 맞는 양과 단위로 자료·질문·정답을 함께 수정한다.'}


class ContentReviewTests(unittest.TestCase):
    def test_review_sees_printed_blanks_and_separate_teacher_answers(self):
        from worksheet_maker import content_review
        from worksheet_maker.schema import Block, Worksheet
        ws = Worksheet(title='빈칸 검토', blocks=[
            Block(type='fill_blank', body='13 × 3 = [[39]]', solution='13을 세 번 더해요.'),
            Block(type='drill', items=['14 × 2 = [[28]]']),
            Block(type='table', rows=[['수량'], ['[[17]]권']]),
            Block(type='short_answer', body='39권이 필요합니다. 30권에서 몇 권 부족한가요?', answer='9권')])
        before = ws.model_dump_json()
        instruction = content_review.prompt(ws, context='3학년', request='', kind='standard', n=3)
        shown = json.loads(instruction.split('검토할 학습지:\n', 1)[1])
        self.assertEqual(shown['blocks'][0]['body'], '13 × 3 = (    )')
        self.assertEqual(shown['blocks'][0]['answer'], '39')
        self.assertEqual(shown['blocks'][1]['items'], ['14 × 2 = (    )'])
        self.assertIn('28', shown['blocks'][1]['answer'])
        self.assertEqual(shown['blocks'][2]['rows'], [['수량'], ['(    )권']])
        self.assertIn('39권이 필요', shown['blocks'][3]['body'])  # 실제 인쇄된 수는 가리지 않는다.
        self.assertIn('학생 문제지에 보이지 않는', instruction)
        self.assertEqual(ws.model_dump_json(), before)

    def test_fusion_review_uses_task_rules_without_generic_inquiry_skeleton(self):
        from worksheet_maker import content_review
        from worksheet_maker.schema import Worksheet
        for task in ('project', 'story', 'inquiry'):
            instruction = content_review.prompt(Worksheet(title='융합'), context='4학년', request='',
                kind='inquiry', n=4, task_type=task, subjects=['수학', '사회'])
            self.assertIn('반드시 지킬 융합 과제 형태', instruction)
            self.assertNotIn('drawing(관찰한 것', instruction)
            self.assertNotIn('나머지 3~5행은 칸을 빈 문자열로', instruction)
        ordinary = content_review.prompt(Worksheet(title='단일'), context='4학년', request='', kind='inquiry', n=4)
        self.assertIn('drawing(관찰한 것', ordinary)

    def test_generation_sdk_limits_internal_retries_on_a_busy_response(self):
        response = httpx.Response(503, json={'error': {'code': 503, 'message': 'busy'}},
                                  request=httpx.Request('POST', 'https://example.invalid'))
        with patch('httpx.Client.send', return_value=response) as send:
            client = llm._generation_client('offline-test-key')
            try:
                with llm.generation_budget(), self.assertRaises(Exception) as raised:
                    llm._generate(client, 'gemini-3.8-flash', '오프라인 점검', [], activity.review_schema())
                self.assertEqual(llm._kind(raised.exception), 'busy')
                # 새 생성 통신은 SDK 안에서 숨은 재시도를 하지 않는다.
                self.assertEqual(send.call_count, 1,
                    [(call.args[0].method, call.args[0].url.path) for call in send.call_args_list])
            finally:
                client.close()

    def test_postprocessing_preserves_references_but_removes_real_number_labels(self):
        texts = ['2번 문제에서 구한 양을 바탕으로 묶음을 골라 보세요.',
                 '문항 3의 자료를 활용해 이유를 써 보세요.',
                 '2번 묶음에 필요한 감자를 더하면 얼마인가요?']
        ws = Worksheet(title='참조 보존', blocks=[Block(type='short_answer', body=t, answer='답') for t in texts]
            + [Block(type='short_answer', body='1번 문항: 520 - 260을 계산해 보세요.', answer='260')])
        llm._postprocess(ws, 3)
        self.assertEqual([b.body for b in ws.blocks[:3]], texts)
        self.assertEqual(ws.blocks[-1].body, '520 - 260을 계산해 보세요.')

    def run_plan(self, outputs):
        with patch.object(llm, '_generate', side_effect=[json.dumps(v) for v in outputs]) as call:
            result = llm._generate_for_request(None, llm.MODELS[0], '원래 지시', request='가족 저녁 식사',
                context='3학년 세 자리 수 덧셈과 뺄셈', n_questions=2, kind="inquiry")
        return result, call

    def test_repair_updates_materials_questions_answers_together_and_is_rechecked(self):
        bad = sample_plan()
        bad['materials'][0]['body'] = '가족 네 명의 한 끼에 달걀 250개를 준비한다.'
        fixed = copy.deepcopy(bad)
        fixed['materials'][0]['body'] = '물을 250mL와 180mL 준비해요.'
        fixed['questions'][0].update(body='430mL의 물을 담을 수 있는 용기를 골라 보세요.',
            items=['400mL', '500mL'], answer='②', solution='500mL 용기에 담을 수 있어요.')
        (raw, _), call = self.run_plan([bad, {'issues': [issue()]}, fixed, {'issues': []}])
        ws = json.loads(raw)
        self.assertIn('250mL', ws['blocks'][1]['body'])
        self.assertEqual(next(b for b in ws['blocks'] if b['type'] == 'multiple_choice')['answer'], '②')
        self.assertEqual(call.call_count, 4)
        repair = call.call_args_list[2].args[2]
        self.assertIn('달걀 250개', repair)
        self.assertIn('자료·조건·질문·선택지·정답·해설', repair)
        self.assertIn('430mL', call.call_args_list[3].args[2])

    def test_still_unrealistic_after_two_repairs_stops(self):
        with self.assertRaisesRegex(llm.AnalyzeError, '충분히 바로잡지 못했어요'):
            self.run_plan([sample_plan(), {'issues': [issue()]}, sample_plan(), {'issues': [issue()]},
                           sample_plan(), {'issues': [issue()]}])

    def test_new_issue_introduced_by_repair_can_be_fixed_once_more(self):
        fixed = sample_plan()
        fixed['title'] = '재검토 지적까지 수정한 자료'
        (raw, _), call = self.run_plan([sample_plan(), {'issues': [issue()]},
            sample_plan(), {'issues': [dict(issue(), category='leakage', question=1)]}, fixed, {'issues': []}])
        self.assertEqual(json.loads(raw)['title'], fixed['title'])
        self.assertEqual(call.call_count, 6)

    def test_failed_review_is_not_treated_as_approval_with_any_model(self):
        with patch.object(llm, '_generate', side_effect=[json.dumps(sample_plan()),
                llm._BrokenOutput('검토 응답 없음'), llm._BrokenOutput('검토 응답 없음'), llm._BrokenOutput('검토 응답 없음')]) as call:
            with self.assertRaises(llm.AnalyzeError):
                llm._generate_for_request(None, llm.MODELS[0], '지시', request='가족 저녁 식사',
                    context='3학년', n_questions=2, kind="inquiry")
        self.assertEqual([c.args[1] for c in call.call_args_list],
                         ['gemini-3.5-flash-lite', *llm.GENERATION_MODELS])

    def test_malformed_review_is_not_an_empty_issue_list(self):
        bad_results = [{}, {'issues': None}, {'issues': [dict(issue(), question=True)]},
                       {'issues': [dict(issue(), question=3)]}, {'issues': [dict(issue(), evidence='')]},
                       {'issues': [dict(issue(), category='unknown')]}]
        for result in bad_results:
            with self.subTest(result=result), self.assertRaises(ValueError):
                activity.parse_review(result, 2)
        self.assertEqual(activity.parse_review({'issues': []}, 2), [])

    def test_repair_cannot_escape_to_non_interest_generation(self):
        with self.assertRaises(llm.AnalyzeError):
            self.run_plan([sample_plan(), {'issues': [issue()]}, {'has_interest': False}, {'has_interest': False}])


if __name__ == '__main__':
    unittest.main()
