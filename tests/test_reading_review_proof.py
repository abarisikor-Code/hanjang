import json
import unittest
from unittest.mock import patch
from worksheet_maker import llm, verify
from worksheet_maker.schema import Worksheet, Block


class ReadingProofTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(title='수 읽기', kind='test', blocks=[Block(type='multiple_choice',
            body='7080000을 바르게 읽은 것을 골라 보세요.', items=['칠십팔만','칠백팔만','칠백팔십만','칠천팔백만'],
            answer='②', solution='700만과 8만을 합한 수이므로 칠백팔만입니다.')])

    def issue(self, category='answer'):
        return dict(category=category, question=1, evidence='7080000은 칠백팔십만이다.', fix='③으로 수정')

    def test_proven_correct_reading_does_not_receive_an_incorrect_ai_repair(self):
        ws = self.worksheet()
        issues, proven = verify.resolve_reading_issues(ws, [self.issue()])
        self.assertEqual(issues, [])
        self.assertEqual(proven, {1})
        self.assertEqual(ws.blocks[0].answer, '②')
        self.assertEqual(ws.blocks[0].solution, '7,080,000은 칠백팔만이라고 읽습니다.')

    def test_scope_and_material_findings_remain_even_when_answer_is_proven(self):
        ws = self.worksheet()
        findings = [self.issue('scope'), self.issue('material'), self.issue()]
        remaining, _ = verify.resolve_reading_issues(ws, findings)
        self.assertEqual(remaining, findings[:2])

    def test_wrong_answer_or_multiple_answers_are_not_silently_approved(self):
        for answer in ('③', '②③'):
            ws = self.worksheet()
            ws.blocks[0].answer = answer
            remaining, proven = verify.resolve_reading_issues(ws, [self.issue()])
            self.assertEqual(remaining, [self.issue()])
            self.assertFalse(proven)
        ws = self.worksheet()
        ws.blocks[0].items = ['칠백팔만', '칠백팔십만', '칠백팔십만']
        ws.blocks[0].answer = '①'
        remaining, proven = verify.resolve_reading_issues(ws, [self.issue()])
        self.assertEqual(remaining, [self.issue()])
        self.assertFalse(proven)

    def test_negative_reading_question_is_not_reversed(self):
        ws = self.worksheet()
        ws.blocks[0].body = '7080000을 잘못 읽은 것을 골라 보세요.'
        ws.blocks[0].items = ['칠백팔만', '칠백팔십만']
        ws.blocks[0].answer = '②'
        before = ws.model_dump()
        remaining, proven = verify.resolve_reading_issues(ws, [self.issue()])
        self.assertFalse(proven)
        self.assertEqual(remaining, [self.issue()])
        self.assertEqual(verify.check_number_words(ws), [])
        self.assertEqual(ws.model_dump(), before)

    def test_program_correction_updates_explanation_with_answer(self):
        ws = self.worksheet()
        ws.blocks[0].answer = '③'
        ws.blocks[0].solution = '700만과 80만을 합한 수이므로 칠백팔십만입니다.'
        notes = verify.check_number_words(ws)
        self.assertTrue(notes)
        self.assertEqual(ws.blocks[0].answer, '② 칠백팔만')
        self.assertEqual(ws.blocks[0].solution, '7,080,000은 칠백팔만이라고 읽습니다.')

    def test_full_ai_review_still_runs_but_false_reading_claim_does_not_trigger_rewrite(self):
        ws = self.worksheet()
        with patch.object(llm, '_generate', side_effect=[ws.model_dump_json(), json.dumps({'issues':[self.issue()]})]) as call:
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '4학년 수 읽기', request='', context='4학년 수학', n_questions=1, kind='test')
        self.assertEqual(call.call_count, 2)
        self.assertIn('issues', call.call_args_list[1].args[4]['properties'])
        result = Worksheet.model_validate_json(raw)
        self.assertEqual(result.blocks[0].answer, '②')
        self.assertIn('칠백팔만', result.blocks[0].solution)
