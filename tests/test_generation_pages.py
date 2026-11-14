"""만들기 제출·화면 갱신·실패 안내를 실제 Streamlit 화면 흐름으로 검사한다."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from worksheet_maker import llm, diagnostics
from worksheet_maker.schema import Block, Worksheet
from repair_fixtures import worksheet_patch


class GenerationPageTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('httpx.Client.send', side_effect=AssertionError('실제 통신 금지'))
        self.send = self.network.start()
        self.addCleanup(self.network.stop)

    def tearDown(self):
        self.assertEqual(self.send.call_count, 0)

    def ready(self):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'), default_timeout=30).run()
        app.session_state['page'] = 'f3'
        app.session_state['grade'] = 3
        app.session_state['fusion_subjects'] = ['국어', '수학']
        app.session_state['n_questions'] = 1
        app.session_state['w_api_key'] = 'offline-local-fake-key'
        app.run()
        self.assertFalse(app.exception)
        return app

    def submit(self, app):
        next(b for b in app.button if b.label.startswith('✨ 융합 과제 만들기')).click().run()
        self.assertFalse(app.exception)

    def worksheet(self):
        return Worksheet(title='가상 융합 초안', kind='inquiry', blocks=[
            Block(type='short_answer', body='23 + 14를 계산해 보세요.', answer='37', solution='37')])

    def result(self):
        return llm.AnalyzeResult(worksheet=self.worksheet(), theme=None, model_used=llm.MODELS[0])

    def test_draft_failure_is_a_persistent_page_and_retry_uses_the_same_request(self):
        app = self.ready()
        app.text_input(key='f_theme_text').set_value('우리 반 책 나눔')
        app.text_input(key='f_extra').set_value('글을 짧게 써 주세요')
        with patch.object(llm, 'generate_integrated', side_effect=llm.AnalyzeError('가상 연결 실패')) as failed:
            self.submit(app)
        self.assertEqual(app.session_state['page'], 'generation_failed')
        self.assertEqual(app.session_state['generation_failure']['request']['theme_text'], '우리 반 책 나눔')
        for _ in range(2):
            app.run()
            self.assertTrue(any('가상 연결 실패' in e.value for e in app.error))
        self.assertEqual(failed.call_count, 1)
        with patch.object(llm, 'generate_integrated', return_value=self.result()) as retry:
            app.button(key='retry_generation').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['page'], 'result')
        self.assertEqual(retry.call_count, 1)
        self.assertEqual(retry.call_args.kwargs, failed.call_args.kwargs)

    def test_screen_rerun_during_draft_is_not_silent_and_does_not_duplicate_requests(self):
        app = self.ready()
        with patch.object(llm, 'generate_integrated', side_effect=lambda **kw: st.rerun()) as call:
            self.submit(app)
            app.run()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(app.session_state['page'], 'generation_failed')
        self.assertTrue(any('화면이 갱신' in e.value for e in app.error))
        self.assertEqual(app.session_state['generation_diagnostics']['status'], 'interrupted')

    def test_enabled_extra_review_does_not_drop_the_completed_result_on_a_review_error(self):
        app = self.ready()
        app.toggle(key='f_review').set_value(True)
        with patch.object(llm, 'generate_integrated', return_value=self.result()) as generation, \
             patch.object(llm, 'review_worksheet', side_effect=llm.AnalyzeError('가상 추가 검토 실패')) as review:
            self.submit(app)
        self.assertEqual(app.session_state['page'], 'result')
        self.assertEqual(generation.call_count, 1)
        self.assertEqual(review.call_count, 1)
        self.assertEqual(app.session_state['generation_diagnostics']['status'], 'success_with_review_warning')

    def test_screen_rerun_during_review_keeps_draft_and_resumes_only_review(self):
        app = self.ready()
        replies = iter([self.worksheet().model_dump_json(), None])
        def generate(*args):
            reply = next(replies)
            if reply is None:
                st.rerun()
            return reply
        with patch.object(llm, '_generation_client', return_value=SimpleNamespace(close=lambda:None)), \
             patch.object(llm, '_generate', side_effect=generate) as call:
            self.submit(app)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(app.session_state['page'], 'review_pending')
        self.assertEqual(app.session_state['pending_generation']['checkpoint']['draft']['title'], self.worksheet().title)
        self.assertTrue(any('화면이 갱신' in e.value for e in app.error))
        with patch.object(llm, '_generation_client', return_value=SimpleNamespace(close=lambda:None)), \
             patch.object(llm, '_generate', return_value='{"issues":[]}') as review:
            app.button(key='resume_review').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['page'], 'result')
        self.assertEqual(review.call_count, 1)
        self.assertIn('issues', review.call_args.args[4]['properties'])

    def test_screen_rerun_before_repair_preserves_findings_and_repair_count(self):
        app = self.ready()
        issue = dict(category='answer', question=1, evidence='검산 필요', fix='정답 검산')
        replies = iter([self.worksheet().model_dump_json(), json.dumps({'issues':[issue]}), None])
        def generate(*args):
            reply = next(replies)
            if reply is None:
                st.rerun()
            return reply
        with patch.object(llm, '_generation_client', return_value=SimpleNamespace(close=lambda:None)), \
             patch.object(llm, '_generate', side_effect=generate):
            self.submit(app)
        pending = app.session_state['pending_generation']['checkpoint']
        self.assertEqual(pending['phase'], 'repair')
        self.assertEqual(pending['issues'], [issue])
        self.assertEqual(pending['attempt'], 0)
        with patch.object(llm, '_generation_client', return_value=SimpleNamespace(close=lambda:None)), \
             patch.object(llm, '_generate', side_effect=[json.dumps(worksheet_patch(self.worksheet())), '{"issues":[]}']) as repair:
            app.button(key='resume_review').click().run()
        self.assertEqual(app.session_state['page'], 'result')
        self.assertEqual(repair.call_count, 2)

    def test_unexpected_exception_shows_safe_failure_and_settings_are_restored(self):
        app = self.ready()
        app.text_input(key='f_theme_text').set_value('우리 반 책 나눔')
        app.text_input(key='f_extra').set_value('짧은 문장')
        with patch.object(llm, 'generate_integrated', side_effect=ValueError('PRIVATE_KEY_AND_TEXT')):
            self.submit(app)
        self.assertEqual(app.session_state['page'], 'generation_failed')
        self.assertNotIn('PRIVATE_KEY_AND_TEXT', '\n'.join(e.value for e in app.error))
        self.assertNotIn('PRIVATE_KEY_AND_TEXT', diagnostics.dumps(app.session_state['generation_diagnostics']))
        app.button(key='failed_settings').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['page'], 'f3')
        self.assertEqual(app.text_input(key='f_theme_text').value, '우리 반 책 나눔')
        self.assertEqual(app.text_input(key='f_extra').value, '짧은 문장')
