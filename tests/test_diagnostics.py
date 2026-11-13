import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from worksheet_maker import diagnostics, llm


class Busy(Exception):
    code = 503


class DiagnosticTests(unittest.TestCase):
    def test_request_metadata_contains_actual_limit_and_lengths_only(self):
        client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(text='{"ok":true}')))
        with diagnostics.capture('1.5.1') as report, llm.generation_budget(150):
            raw = llm._generate(client, llm.MODELS[0], 'PRIVATE_PROMPT', [], {'type': 'object'})
            diagnostics.finish('success')
        self.assertEqual(raw, '{"ok":true}')
        row = next(r for r in report['events'] if r['event'] == 'request_settings')
        self.assertEqual(row['prompt_chars'], len('PRIVATE_PROMPT'))
        self.assertEqual(row['timeout_seconds'], 60)
        self.assertEqual(row['thinking'], llm.THINKING)
        self.assertNotIn('PRIVATE_PROMPT', diagnostics.dumps(report))

    def test_all_stages_and_failover_are_recorded_without_exception_or_content(self):
        clock = [0.0]
        def call(model):
            clock[0] += 3
            if model == llm.MODELS[0]:
                raise Busy('SECRET_KEY and private worksheet text')
            return 'private response'
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]), patch.object(llm.time, 'sleep'):
            with diagnostics.capture('1.5.1') as report, llm.generation_budget(150):
                for stage in ('1단계 · 초안 만들기', '2단계 · 내용 검토', '검토 후 수정'):
                    llm._with_retry(llm.GENERATION_MODELS[:2], call, stage=stage)
                diagnostics.finish('success')
        rows = [r for r in report['events'] if r['event'] == 'request_end']
        self.assertEqual([r['stage'] for r in rows], ['draft', 'draft', 'review', 'review', 'repair', 'repair'])
        self.assertEqual(rows[0]['http_status'], 503)
        self.assertEqual(rows[0]['duration_seconds'], 3)
        self.assertEqual(rows[1]['outcome'], 'success')
        for secret in ('SECRET_KEY', 'private worksheet text', 'private response'):
            self.assertNotIn(secret, diagnostics.dumps(report))
        self.assertTrue(all('remaining_seconds' in row for row in rows))

    def test_resume_keeps_history_but_new_work_and_other_contexts_do_not_share_it(self):
        with diagnostics.capture('1.5.1') as first:
            diagnostics.emit('request_start', stage='2단계 · 내용 검토')
            diagnostics.finish('pending_review')
        before = json.loads(diagnostics.dumps(first))
        with diagnostics.capture('1.5.1', first) as resumed:
            diagnostics.finish('success')
        self.assertEqual(first, before)
        self.assertEqual(resumed['work_id'], first['work_id'])
        self.assertEqual(resumed['attempt'], 2)
        self.assertGreater(len(resumed['events']), len(first['events']))
        with diagnostics.capture('1.5.1') as other:
            diagnostics.finish('failed')
        self.assertNotEqual(other['work_id'], first['work_id'])
        diagnostics.emit('request_start')
        self.assertEqual(first, before)

    def test_unapproved_strings_are_dropped_and_unexpected_exit_is_marked(self):
        with diagnostics.capture('malicious key') as report:
            diagnostics.emit('request_settings', model='secret-key', stage='private content',
                             prompt='secret', error='secret', thinking='secret', prompt_chars=123)
        exported = diagnostics.dumps(report)
        self.assertNotIn('secret', exported)
        self.assertNotIn('private content', exported)
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual(report['events'][1]['prompt_chars'], 123)

    def test_exhausted_budget_is_visible_without_another_request(self):
        clock = [0.0]
        with patch.object(llm.time, 'perf_counter', side_effect=lambda: clock[0]):
            with diagnostics.capture('1.5.1') as report, llm.generation_budget(10):
                clock[0] = 11
                with self.assertRaises(llm.AnalyzeError):
                    llm._with_retry(llm.GENERATION_MODELS, lambda m: self.fail('must not call'), stage='2단계 · 내용 검토')
                diagnostics.finish('pending_review')
        self.assertTrue(any(r['event'] == 'budget_stop' and r['remaining_seconds'] == 0 for r in report['events']))
        self.assertFalse(any(r['event'] == 'request_start' for r in report['events']))
