import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from worksheet_maker import llm
from worksheet_maker.schema import Block, Worksheet
from test_activity_design import sample_plan
from repair_fixtures import worksheet_patch


class Busy(Exception):
    code = 503


class ReviewResumeTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(kind='standard', title='보관할 초안', blocks=[
            Block(type='concept', body='같은 자릿수끼리 더해요.'),
            Block(type='short_answer', body='234 + 125를 계산해 보세요.', answer='359', solution='234+125=359')])

    def interrupted(self, *, activity=False):
        draft = json.dumps(sample_plan()) if activity else self.worksheet().model_dump_json()
        with patch.object(llm, '_generate', side_effect=[draft, Busy(), Busy(), Busy(), Busy()]), patch.object(llm.time, 'sleep'):
            with self.assertRaises(llm.ReviewInterrupted) as raised:
                llm._generate_for_request(None, llm.MODELS[0], '수업 조건', request='박물관', context='3학년',
                    n_questions=2 if activity else 1, kind='inquiry' if activity else 'standard')
        return raised.exception.checkpoint

    def resume(self, checkpoint, outputs):
        client = SimpleNamespace(close=lambda: None)
        with patch.object(llm, '_generation_client', return_value=client), patch.object(llm, '_generate', side_effect=outputs) as call:
            result = llm.resume_generation(api_key='offline-key', checkpoint=checkpoint, grade=3)
        return result, call

    def test_failed_review_keeps_original_and_resume_never_generates_another_initial_draft(self):
        checkpoint = self.interrupted()
        before = copy.deepcopy(checkpoint)
        self.assertEqual(checkpoint['draft'], self.worksheet().model_dump())
        result, call = self.resume(checkpoint, ['{"issues":[]}'])
        self.assertEqual(call.call_count, 1)
        self.assertIn('issues', call.call_args.args[4]['properties'])
        self.assertEqual(result.worksheet, self.worksheet())
        self.assertEqual(checkpoint, before)
        self.assertNotIn('offline-key', json.dumps(checkpoint))

    def test_resume_has_a_fresh_budget_and_does_not_hide_a_second_failure(self):
        checkpoint = self.interrupted()
        clock = [900.0]
        budgets = []
        def inspect(*args):
            budgets.append(llm._remaining_time())
            raise Busy()
        client = SimpleNamespace(close=lambda: None)
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm.time, 'sleep'), \
                patch.object(llm, '_generation_client', return_value=client), patch.object(llm, '_generate', side_effect=inspect):
            with self.assertRaises(llm.ReviewInterrupted) as raised:
                llm.resume_generation(api_key='offline-key', checkpoint=checkpoint, grade=3)
        self.assertEqual(raised.exception.checkpoint['draft'], checkpoint['draft'])
        self.assertEqual(budgets, [llm.REVIEW_TIME_BUDGET] * 4)
        self.assertIsNone(llm._remaining_time())

    def test_failed_repair_resumes_pending_repair_then_reviews_it(self):
        checkpoint = self.interrupted()
        issue = dict(category='answer', question=1, evidence='정답 검산 필요', fix='정답을 다시 계산한다.')
        with patch.object(llm, '_generate', side_effect=[json.dumps({'issues':[issue]}), Busy(), Busy(), Busy(), Busy()]), patch.object(llm.time, 'sleep'):
            with self.assertRaises(llm.ReviewInterrupted) as raised:
                llm._review_saved_draft(None, checkpoint)
        pending = raised.exception.checkpoint
        self.assertEqual(pending['phase'], 'repair')
        self.assertEqual(pending['issues'], [issue])
        _, call = self.resume(pending, [json.dumps(worksheet_patch(self.worksheet())), '{"issues":[]}'])
        self.assertEqual(call.call_count, 2)
        self.assertIn('block_edits', call.call_args_list[0].args[4]['properties'])
        self.assertIn('issues', call.call_args_list[1].args[4]['properties'])

    def test_latest_repaired_draft_and_repair_limit_survive_interruption(self):
        checkpoint = self.interrupted()
        fixed = self.worksheet()
        fixed.title = '마지막 수정본'
        checkpoint.update(attempt=1, phase='repair', issues=[dict(category='material', question=0, evidence='자료', fix='수정')])
        with patch.object(llm, '_generate', side_effect=[json.dumps(worksheet_patch(fixed)), Busy(), Busy(), Busy(), Busy()]), patch.object(llm.time, 'sleep'):
            with self.assertRaises(llm.ReviewInterrupted) as raised:
                llm._review_saved_draft(None, checkpoint)
        pending = raised.exception.checkpoint
        self.assertEqual(pending['draft']['title'], fixed.title)
        self.assertEqual(pending['attempt'], 2)
        bad_review = json.dumps({'issues':[dict(category='answer', question=1, evidence='남은 문제', fix='재수정 필요')]})
        with self.assertRaises(llm.AnalyzeError) as error:
            self.resume(pending, [bad_review])
        self.assertNotIsInstance(error.exception, llm.ReviewInterrupted)

    def test_inquiry_resume_keeps_the_activity_materials_and_purpose(self):
        checkpoint = self.interrupted(activity=True)
        self.assertEqual(checkpoint['mode'], 'activity')
        self.assertEqual(checkpoint['draft']['materials'], sample_plan()['materials'])
        _, call = self.resume(checkpoint, ['{"issues":[]}'])
        self.assertEqual(call.call_count, 1)
        self.assertIn(sample_plan()['purpose'], call.call_args.args[2])

    def test_busy_fallback_waits_but_does_not_add_calls_or_ignore_budget(self):
        sleeps = []
        with patch.object(llm.time, 'sleep', side_effect=sleeps.append):
            with self.assertRaises(llm.AnalyzeError):
                llm._with_retry(['a','b','c'], lambda _: (_ for _ in ()).throw(Busy()))
        self.assertEqual(sleeps, [2,4])
        clock = [0.0]
        calls = []
        def call(model):
            calls.append(model)
            clock[0] = 9.5
            raise Busy()
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), llm.generation_budget(10):
            with self.assertRaisesRegex(llm.AnalyzeError, '대기 시간이 부족'):
                llm._with_retry(['a','b'], call)
        self.assertEqual(calls, ['a'])

    def test_tiny_remaining_budget_never_sends_a_request(self):
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **_: self.fail('요청을 보내면 안 됩니다.')))
        with llm.generation_budget(0.1), self.assertRaises(llm.AnalyzeError):
            llm._generate(client, llm.MODELS[0], '검토', [], {})

    def test_inner_budget_expiry_preserves_previous_model_failure_details(self):
        def call(model):
            if model == 'a':
                raise Busy()
            raise llm._BudgetExpired('전체 대기 시간 만료')
        with patch.object(llm.time, 'sleep'), self.assertRaises(llm.AnalyzeError) as raised:
            llm._with_retry(['a','b'], call, stage='내용 검토')
        self.assertIn('(503)', str(raised.exception))
        self.assertIn('전체 대기 시간 만료', str(raised.exception))
