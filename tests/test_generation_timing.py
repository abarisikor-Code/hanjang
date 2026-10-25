import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from worksheet_maker import llm
from worksheet_maker.schema import Block, Worksheet


class GenerationTimingTests(unittest.TestCase):
    def test_mixed_failures_keep_stage_quota_and_timeout_without_raw_exception(self):
        class QuotaError(Exception):
            code = 429
        def call(model):
            if model == 'first':
                raise QuotaError('private-request-and-key')
            raise httpx.ReadTimeout('private-request-and-key')
        with self.assertRaises(llm.AnalyzeError) as raised:
            llm._with_retry(['first', 'second'], call, stage='2단계 · 내용 검토')
        message = str(raised.exception)
        self.assertIn('2단계 · 내용 검토', message)
        self.assertIn('first: 사용 한도 초과 (429)', message)
        self.assertIn('second: 응답 시간 초과', message)
        self.assertNotIn('private-request-and-key', message)

    def test_request_timeout_uses_remaining_shared_budget_and_preserves_thinking(self):
        clock = [0.0]
        configs = []
        durations = iter([70.0, 20.0])
        def send(**kwargs):
            configs.append(kwargs['config'])
            clock[0] += next(durations)
            return SimpleNamespace(text='{"ok": true}')
        client = SimpleNamespace(models=SimpleNamespace(generate_content=send))
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]):
            with llm.generation_budget(100):
                llm._generate(client, 'model', '첫 단계', [], {})
                with llm.generation_budget(999):  # 검토 단계에서 시간 예산을 다시 시작하지 않는다.
                    llm._generate(client, 'model', '둘째 단계', [], {})
                self.assertEqual(llm._remaining_time(), 10)
        self.assertIsNone(llm._remaining_time())
        self.assertEqual([c['http_options']['timeout'] for c in configs], [60000, 30000])
        self.assertTrue(all(c['http_options']['retry_options']['attempts'] == 1 for c in configs))
        self.assertTrue(all(c['thinking_config']['thinking_level'] == llm.THINKING for c in configs))

    def test_exhausted_budget_stops_before_another_model(self):
        clock = [0.0]
        calls = []
        def call(model):
            calls.append(model)
            clock[0] = 11
            raise httpx.ReadTimeout('timed out')
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), llm.generation_budget(10):
            with self.assertRaisesRegex(llm.AnalyzeError, '전체 대기 시간'):
                llm._with_retry(['first', 'second'], call)
        self.assertEqual(calls, ['first'])

    def test_review_finishing_after_budget_cannot_return_a_worksheet(self):
        clock = [0.0]
        ws = Worksheet(title='기본', blocks=[Block(type='concept', items=['덧셈: 모으기']),
            Block(type='short_answer', body='2 + 3', answer='5')])
        def generate(*args):
            if 'issues' in args[4]['properties']:
                clock[0] = 151
                return '{"issues": []}'
            clock[0] = 50
            return ws.model_dump_json()
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm, '_generate', side_effect=generate):
            with self.assertRaisesRegex(llm.AnalyzeError, '전체 대기 시간'):
                llm._generate_for_request(None, llm.MODELS[0], '지시', request='', context='1학년', n_questions=1)

    def test_fallback_reason_is_not_always_server_congestion(self):
        for error, expected in [(httpx.ReadTimeout('timed out'), '응답 대기 시간이'),
                                 (llm._BrokenOutput('JSON'), '응답 형식이')]:
            notices = []
            with self.subTest(error=error), patch.object(llm, '_notify', side_effect=notices.append):
                def call(model):
                    if model == 'first':
                        raise error
                    return 'ok'
                self.assertEqual(llm._with_retry(['first', 'second'], call, stage='내용 검토')[0], 'ok')
                self.assertIn(expected, notices[-1])
                self.assertIn('내용 검토', notices[-1])
                self.assertNotIn('붐벼서', notices[-1])

    def test_selected_model_is_first_and_available_models_are_not_excluded(self):
        self.assertEqual(llm._design_order(llm.MODELS[0]), llm.GENERATION_MODELS)
        self.assertEqual(llm._design_order('gemini-3.8-flash'), ['gemini-3.8-flash', 'gemini-3.5-flash-lite', 'gemini-3.6-flash'])
        self.assertEqual(llm._design_order('gemini-3.6-flash'), ['gemini-3.6-flash', 'gemini-3.5-flash-lite', 'gemini-3.8-flash'])
        self.assertEqual(llm.GENERATION_MODELS[0], 'gemini-3.5-flash-lite')

    def test_sdk_uses_single_standard_request_for_timeout_and_valid_json(self):
        request = httpx.Request('POST', 'https://example.invalid')
        payload = {'candidates': [{'content': {'parts': [{'text': '{"ok":true}'}], 'role': 'model'}}]}
        for response in [httpx.Response(200, json=payload, request=request), httpx.ReadTimeout('timed out')]:
            with self.subTest(response=response), patch('httpx.Client.send') as send:
                if isinstance(response, Exception):
                    send.side_effect = response
                else:
                    send.return_value = response
                client = llm._generation_client('offline-test-key')
                try:
                    with llm.generation_budget(10):
                        if isinstance(response, Exception):
                            with self.assertRaises(Exception) as raised:
                                llm._generate(client, 'gemini-3.6-flash', '검사', [], {'type':'object'})
                            self.assertEqual(llm._kind(raised.exception), 'timeout')
                        else:
                            self.assertEqual(json.loads(llm._generate(client, 'gemini-3.6-flash', '검사', [], {'type':'object'})), {'ok':True})
                    self.assertEqual(send.call_count, 1)
                    sent = send.call_args.args[0]
                    self.assertIn(':generateContent', sent.url.path)
                    self.assertLessEqual(sent.extensions['timeout']['read'], 10)
                finally:
                    client.close()
