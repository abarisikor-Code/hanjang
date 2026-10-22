import json
import unittest
from unittest.mock import patch

from worksheet_maker import activity, content_review, kinds, llm
from worksheet_maker.schema import Worksheet, Block


class FourKindsTests(unittest.TestCase):
    def test_new_selection_retains_old_file_types(self):
        self.assertEqual(len(kinds.ACTIVE_KINDS), 4)
        for old, new in [('note', 'standard'), ('game', 'drill')]:
            self.assertNotIn(old, kinds.ACTIVE_KINDS)
            self.assertEqual(kinds.for_generation(old), new)
            self.assertEqual(Worksheet(title='예전 파일', kind=old).kind, old)

    def test_drill_counts_items_and_accepts_intended_repetition(self):
        ws = Worksheet(title='연습', blocks=[Block(type='drill', items=['2 + 3 = [[5]]', '3 + 4 = [[7]]'])])
        self.assertEqual(content_review.question_count(ws), 2)
        self.assertEqual(content_review.structure_issues(ws, 'drill', 2), [])
        self.assertIn('반복 자체를 오류라 하지 말고', content_review.prompt(ws, context='1학년', request='', kind='drill', n=2))

    def test_plain_exam_and_separated_subjects_cannot_skip_review(self):
        ws = Worksheet(title='확인', kind='test', blocks=[Block(type='short_answer', body='2 + 3', answer='5')])
        for separate in (True, False):
            with self.subTest(separate=separate), patch.object(llm, '_generate', side_effect=[ws.model_dump_json(), '{"issues": []}']) as call:
                raw, _ = llm._generate_for_request(None, llm.MODELS[0], '시험 지시', request='',
                    context='1학년', n_questions=1, kind='test', plan_activity=not separate)
                self.assertEqual(json.loads(raw)['kind'], 'test')
                self.assertEqual(call.call_count, 2)

    def test_structural_problem_requires_repair_even_if_ai_passes(self):
        bad = Worksheet(title='개념', blocks=[Block(type='short_answer', body='2 + 3', answer='5')])
        fixed = bad.model_copy(deep=True)
        fixed.blocks.insert(0, Block(type='concept', items=['덧셈: 두 수를 모읍니다.']))
        outputs = [bad.model_dump_json(), '{"issues": []}', fixed.model_dump_json(), '{"issues": []}']
        with patch.object(llm, '_generate', side_effect=outputs) as call:
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '개념 지시', request='',
                context='1학년', n_questions=1)
        self.assertEqual(call.call_count, 4)
        self.assertEqual(json.loads(raw)['blocks'][0]['type'], 'concept')

    def test_unresolved_general_review_stops_and_malformed_review_cannot_pass(self):
        ws = Worksheet(title='확인', blocks=[Block(type='short_answer', body='2+3', answer='5')])
        problem = dict(category='answer', question=1, evidence='잘못된 답', fix='다시 풀기')
        outputs = [ws.model_dump_json(), json.dumps({'issues': [problem]})] * 3
        with patch.object(llm, '_generate', side_effect=outputs), self.assertRaises(llm.AnalyzeError):
            llm._generate_for_request(None, llm.MODELS[0], '지시', request='', context='1학년', n_questions=1, kind='test')
        with patch.object(llm, '_generate', side_effect=[ws.model_dump_json(), '{}', '{}']), self.assertRaises(llm.AnalyzeError):
            llm._generate_for_request(None, llm.MODELS[0], '지시', request='', context='1학년', n_questions=1, kind='test')
