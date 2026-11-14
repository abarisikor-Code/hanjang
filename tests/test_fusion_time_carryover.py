import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from worksheet_maker import llm
from worksheet_maker.schema import Worksheet, Block


class Busy(Exception):
    code = 503


class FusionTimeTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(title='가상 융합', kind='inquiry', blocks=[
            Block(type='essay', body='자료의 두 방법을 비교해 보세요.', answer='예시 답', solution='자료에 근거한 판단')])

    def delta(self, text):
        block = self.worksheet().blocks[0].model_copy(update={'body': text})
        return json.dumps({'header': {}, 'block_edits': [{'at':0, 'delete':1, 'values':[block.model_dump()]}]})

    def test_busy_review_then_second_repair_uses_remaining_total_and_healthy_model(self):
        # 서버 대기는 가상 시계로 재현한다. 실제 통신은 하지 않는다.
        clock, observed = [0.0], []
        problem = dict(category='material', question=1, evidence='자료 확인 필요', fix='자료에 맞게 문항 수정')
        replies = [self.worksheet().model_dump_json(), json.dumps({'issues':[problem]}), self.delta('첫 번째 수정'),
                   Busy(), Busy(), json.dumps({'issues':[problem]}), self.delta('두 번째 수정'), '{"issues":[]}']
        durations = [15, 11, 22, 8, 8, 28, 30, 10]
        def generate(client, model, prompt, files, schema):
            index = len(observed)
            observed.append((model, llm._remaining_time()))
            clock[0] += durations[index]
            reply = replies[index]
            if isinstance(reply, Exception):
                raise reply
            return reply
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(llm.time, 'perf_counter', side_effect=lambda:clock[0]), \
             patch.object(llm.time, 'sleep', side_effect=sleep), patch.object(llm.random, 'uniform', return_value=2.5), \
             patch.object(llm, '_generate', side_effect=generate):
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '가상 융합 조건', request='', context='4학년 국어·수학',
                n_questions=1, kind='inquiry', task_type='project', subjects=['국어','수학'])
        self.assertEqual([model for model, _ in observed], [llm.MODELS[0]] * 5 + ['gemini-3.6-flash'] * 3)
        self.assertGreater(observed[6][1], 100)
        self.assertEqual(clock[0], 138.5)
        self.assertLess(clock[0], llm.GENERATION_TIME_BUDGET)
        self.assertEqual(Worksheet.model_validate_json(raw).blocks[0].body, '두 번째 수정')

    def test_fast_draft_can_donate_more_than_120_seconds_to_review(self):
        clock, remaining = [0.0], []
        def generate(*args):
            if 'issues' in args[4]['properties']:
                remaining.append(llm._remaining_time())
                clock[0] += 130
                return '{"issues":[]}'
            clock[0] += 15
            return self.worksheet().model_dump_json()
        with patch.object(llm.time, 'perf_counter', side_effect=lambda:clock[0]), patch.object(llm, '_generate', side_effect=generate):
            llm._generate_for_request(None, llm.MODELS[0], '융합 조건', request='', context='4학년',
                n_questions=1, kind='inquiry', task_type='story', subjects=['국어','수학'])
        self.assertEqual(remaining, [195])
        self.assertEqual(clock[0], 145)

    def checkpoint(self):
        return dict(mode='worksheet', draft=self.worksheet().model_dump(), used=llm.MODELS[0], requested_model=llm.MODELS[0],
            preferred='gemini-3.6-flash', prompt='가상 조건', request='', context='4학년', n_questions=1, kind='inquiry',
            task_type='project', subjects=['국어','수학'], phase='repair', attempt=0,
            issues=[dict(category='material', question=1, evidence='자료', fix='수정')])

    def test_manual_resume_keeps_healthy_model_unless_user_changes_selection(self):
        for selected, expected in ((llm.MODELS[0], 'gemini-3.6-flash'), ('gemini-3.8-flash', 'gemini-3.8-flash')):
            with self.subTest(selected=selected), \
                 patch.object(llm, '_generation_client', return_value=SimpleNamespace(close=lambda:None)), \
                 patch.object(llm, '_generate', side_effect=[self.delta('수정한 문항'), '{"issues":[]}']) as call:
                llm.resume_generation(api_key='offline-only', checkpoint=self.checkpoint(), grade=4, model=selected)
            self.assertEqual([c.args[1] for c in call.call_args_list], [expected, expected])

    def test_manual_resume_still_stops_at_120_seconds_without_bypassing_review(self):
        clock = [0.0]
        saved = self.checkpoint()
        saved['phase'] = 'review'
        def generate(*args):
            clock[0] += 121
            return '{"issues":[]}'
        with patch.object(llm.time, 'perf_counter', side_effect=lambda:clock[0]), \
             patch.object(llm, '_generation_client', return_value=SimpleNamespace(close=lambda:None)), \
             patch.object(llm, '_generate', side_effect=generate) as call:
            with self.assertRaises(llm.ReviewInterrupted):
                llm.resume_generation(api_key='offline-only', checkpoint=saved, grade=4)
        self.assertEqual(call.call_count, 1)
