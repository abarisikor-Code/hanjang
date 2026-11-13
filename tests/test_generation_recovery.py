import json
import unittest
from unittest.mock import patch
import httpx

from worksheet_maker import llm, diagnostics
from worksheet_maker.schema import Worksheet, Block


class Busy(Exception):
    code = 503


class RecoveryTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(title='가상 덧셈', blocks=[Block(type='concept', body='같은 자릿수끼리 더해요.'),
            Block(type='short_answer', body='23 + 14를 계산해 보세요.', answer='37', solution='37')])

    def test_real_sdk_recovers_review_503_with_three_http_requests_total(self):
        sent = []
        ws = self.worksheet()
        def send(request, **kwargs):
            sent.append(json.loads(request.content))
            if len(sent) == 2:
                return httpx.Response(503, json={'error': {'code': 503, 'message': 'busy'}}, request=request)
            text = '{"issues":[]}' if len(sent) == 3 else ws.model_dump_json()
            return httpx.Response(200, json={'candidates':[{'content':{'parts':[{'text':text}]}}]}, request=request)
        client = llm._generation_client('offline-test-key')
        try:
            with patch('httpx.Client.send', side_effect=send), patch.object(llm.time, 'sleep'):
                raw, _ = llm._generate_for_request(client, llm.MODELS[0], '가상 지시', request='', context='3학년', n_questions=1)
        finally:
            client.close()
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[1], sent[2])
        self.assertEqual(Worksheet.model_validate_json(raw).title, ws.title)

    def test_fast_503_recovers_on_the_same_model_without_regeneration(self):
        clock, called, waits = [0.0], [], []
        def request(model):
            called.append(model)
            clock[0] += 5
            if len(called) == 1:
                raise Busy()
            return 'review-result'
        def sleep(seconds):
            waits.append(seconds)
            clock[0] += seconds
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm.time, 'sleep', side_effect=sleep):
            with diagnostics.capture('1.5.1') as report, llm.generation_budget():
                result, model = llm._with_retry(llm.GENERATION_MODELS, request, stage='2단계 · 내용 검토', recover_busy=True)
        self.assertEqual(result, 'review-result')
        self.assertEqual(called, [llm.MODELS[0]] * 2)
        self.assertEqual(model, llm.MODELS[0])
        self.assertEqual(len(waits), 1)
        self.assertTrue(2 <= waits[0] <= 3)
        self.assertEqual([r['outcome'] for r in report['events'] if r['event'] == 'request_end'], ['busy', 'success'])

    def test_recovery_has_at_most_one_extra_call_and_obeys_the_budget(self):
        called = []
        def request(model):
            called.append(model)
            raise Busy()
        with patch.object(llm.time, 'sleep'), llm.generation_budget():
            with self.assertRaises(llm.AnalyzeError):
                llm._with_retry(llm.GENERATION_MODELS, request, recover_busy=True)
        self.assertEqual(called, [llm.MODELS[0], *llm.GENERATION_MODELS])
        called.clear()
        with patch.object(llm.time, 'sleep'), llm.generation_budget(20):
            with self.assertRaises(llm.AnalyzeError):
                llm._with_retry(llm.GENERATION_MODELS, request, recover_busy=True)
        self.assertEqual(called, llm.GENERATION_MODELS)

    def test_slow_503_timeouts_and_quota_do_not_repeat_the_same_model(self):
        for code, duration in ((503, 16), (504, 2), (429, 2), (403, 2)):
            clock, called = [0.0], []
            error = type('ServiceError', (Exception,), {'code': code})
            def request(model):
                called.append(model)
                clock[0] += duration
                raise error()
            with self.subTest(code=code), patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm.time, 'sleep'):
                with llm.generation_budget(), self.assertRaises(llm.AnalyzeError):
                    llm._with_retry(llm.GENERATION_MODELS, request, recover_busy=True)
            self.assertEqual(called, [llm.MODELS[0]] if code == 403 else llm.GENERATION_MODELS)

    def test_slow_draft_does_not_take_the_review_window(self):
        clock, windows = [0.0], []
        ws = self.worksheet()
        def request(*args):
            if 'issues' in args[4]['properties']:
                windows.append(llm._remaining_time())
                clock[0] += 119
                return '{"issues":[]}'
            windows.append(llm._remaining_time())
            clock[0] += 80
            return ws.model_dump_json()
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm, '_generate', side_effect=request):
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '가상 지시', request='', context='3학년', n_questions=1)
        self.assertEqual(windows, [90, 120])
        self.assertEqual(Worksheet.model_validate_json(raw).title, ws.title)
        self.assertEqual(clock[0], 199)

    def test_repairs_do_not_refresh_the_review_window_or_bypass_review(self):
        clock, windows = [0.0], []
        ws = self.worksheet()
        count = [0]
        issue = dict(category='answer', question=1, evidence='검산 필요', fix='정답 검산')
        def request(*args):
            count[0] += 1
            windows.append(llm._remaining_time())
            clock[0] += [50, 60, 50, 11][count[0] - 1]
            if count[0] == 2:
                return json.dumps({'issues': [issue]})
            return '{"issues":[]}' if count[0] == 4 else ws.model_dump_json()
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm, '_generate', side_effect=request):
            with self.assertRaises(llm.ReviewInterrupted) as raised:
                llm._generate_for_request(None, llm.MODELS[0], '가상 지시', request='', context='3학년', n_questions=1)
        self.assertEqual(windows, [90, 120, 60, 10])
        self.assertEqual(raised.exception.checkpoint['attempt'], 1)

    def test_phase_window_never_extends_an_existing_outer_deadline(self):
        clock = [0.0]
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]):
            with llm.generation_budget(100):
                clock[0] = 80
                with llm.generation_phase_budget(120):
                    self.assertEqual(llm._remaining_time(), 20)
                    clock[0] = 101
                    with self.assertRaises(llm.AnalyzeError):
                        llm._remaining_time()

    def test_initial_preparation_counts_against_draft_not_review(self):
        clock = [0.0]
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]):
            with llm.generation_budget():
                clock[0] = 24
                with llm.generation_phase_budget(llm.DRAFT_TIME_BUDGET, from_start=True):
                    self.assertEqual(llm._remaining_time(), 66)
                    clock[0] = 90
                with llm.generation_phase_budget(llm.REVIEW_TIME_BUDGET):
                    self.assertEqual(llm._remaining_time(), 120)
