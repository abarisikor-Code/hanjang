import json
import unittest
from unittest.mock import patch

from worksheet_maker import curriculum, fusion, handoff, llm
from worksheet_maker.render import render
from worksheet_maker.saved import unpack
from worksheet_maker.schema import Block, Worksheet


class FusionTests(unittest.TestCase):
    def test_task_survives_generation_review_and_repair(self):
        ws = Worksheet(title='이야기', kind='inquiry', blocks=[Block(type='essay', body='사건의 해결을 설명하세요.', answer='답')])
        issue = {'category': 'linkage', 'question': 1, 'evidence': '사건이 이어지지 않음', 'fix': '같은 사건으로 연결'}
        outputs = [ws.model_dump_json(), json.dumps({'issues': [issue]}), ws.model_dump_json(), '{"issues": []}']
        with patch.object(llm, '_generate', side_effect=outputs) as call:
            llm._generate_for_request(None, llm.MODELS[0], fusion.rule('story', ['국어', '수학'], 'inquiry'),
                request='축구', context='3학년', n_questions=1, kind='inquiry', task_type='story', subjects=['국어', '수학'])
        self.assertEqual(call.call_count, 4)
        for c in call.call_args_list:
            self.assertIn('이야기 속 문제 풀이', c.args[2])
            self.assertIn('사건', c.args[2])

    def test_integrated_forms_take_priority_and_keep_separate_kind(self):
        for task in fusion.TASK_TYPES:
            with self.subTest(task=task), patch('google.genai.Client'), patch.object(llm, '_generate_for_request',
                    return_value=(Worksheet(title='전달 검사').model_dump_json(), '시험 모델')) as call:
                result = llm.generate_integrated(api_key='가짜 키', grade=3,
                    picks={'수학': curriculum.find(['4수01-04']), '국어': curriculum.find(['4국02-02'])},
                    candidates={}, task_type=task, kind='test')
            self.assertEqual(call.call_args.kwargs['task_type'], task)
            self.assertEqual(call.call_args.kwargs['subjects'], ['수학', '국어'])
            self.assertIn(fusion.TASK_TYPES[task][0], call.call_args.args[2])
            self.assertEqual(result.worksheet.kind, 'test' if task == 'separate' else 'inquiry')


class HandoffTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(title='편집한 학습지', blocks=[
            Block(type='fill_blank', body='빈칸을 채우세요. [[고유정답가]]', answer='고유정답가', solution='고유해설가'),
            Block(type='drill', items=['3 + 4 = [[고유정답나]]'], answer='고유정답나', solution='고유해설나'),
            Block(type='short_answer', body='이유를 설명하세요.', answer='고유정답다', solution='고유해설다')])

    def test_questions_export_removes_answers_from_entire_html_and_does_not_mutate_original(self):
        ws = self.worksheet()
        before = ws.model_dump_json()
        html = handoff.export_html(ws)
        for secret in ['고유정답가', '고유정답나', '고유정답다', '고유해설가', '고유해설나', '고유해설다', 'id="hanjang-data"']:
            self.assertNotIn(secret, html)
            self.assertNotIn(secret, handoff.snapshot(ws))
        self.assertIn('빈칸을 채우세요', html)
        self.assertEqual(ws.model_dump_json(), before)
        restored = unpack('원본.html', render(ws).encode()).worksheet
        self.assertEqual(restored, ws)

    def test_answer_option_and_signature_detect_current_edits(self):
        ws = self.worksheet()
        self.assertIn('고유정답가', handoff.export_html(ws, include_answers=True))
        sig = handoff.fingerprint(ws, False, False, 'html')
        ws.blocks[0].body += ' 방금 수정한 내용'
        self.assertNotEqual(sig, handoff.fingerprint(ws, False, False, 'html'))
        self.assertNotEqual(sig, handoff.fingerprint(self.worksheet(), True, False, 'html'))

    def test_ai_recommendation_reads_final_content_and_appends_fixed_constraints(self):
        ws = self.worksheet()
        ws.blocks[-1].body = '방금 고친 문항 내용'
        with patch.object(llm, '_generation_client'), patch.object(llm, '_generate',
                return_value=json.dumps({'request': '읽기 자료는 구분하고 서술형 답 공간을 넉넉히 둬 주세요.'})) as call:
            result = llm.suggest_styling_prompt(api_key='가짜 키', worksheet=ws)
        self.assertIn('방금 고친 문항 내용', call.call_args.args[2])
        self.assertNotIn('고유정답', call.call_args.args[2])
        self.assertIn('전체 콘셉트', call.call_args.args[2])
        self.assertIn('캐릭터', call.call_args.args[2])
        self.assertIn('현재 학습지의 구체적인 영역·문항', call.call_args.args[2])
        self.assertIn('정답·해설·풀이 시범·답지', result)
        with self.assertRaises(llm.AnalyzeError):
            llm.suggest_styling_prompt(api_key='', worksheet=ws)

    def test_prompt_modes_keep_color_flexible_pages_and_answer_scope(self):
        for answers in (False, True):
            for reference in (False, True):
                for output in ('image', 'html'):
                    with self.subTest(answers=answers, reference=reference, output=output):
                        text = handoff.fixed_instructions(answers, reference, output)
                        self.assertIn('컬러', text)
                        self.assertIn('쪽수에 제한을 두지 말고', text)
                        self.assertIn('실제 쪽 배정과 전체 쪽수는 고정하지 않으며', text)
                        self.assertIn('문항 수·순서·선택지·단위', text)
                        self.assertNotIn('흑백 인쇄', text)
                        self.assertIn('답지는 별도 쪽' if answers else '문제 부분만', text)
                        self.assertIn('참고 양식 파일도' if reference else '직접 선택', text)
                        self.assertIn('각 페이지 전체' if output=='image' else 'HTML 파일', text)
        self.assertIn('원문에 이미 있는 개념 설명·풀이 예시는 그대로 보존', handoff.fixed_instructions(False,False,'image'))

    def test_changed_prompt_policy_invalidates_existing_recommendation(self):
        ws = self.worksheet()
        old = handoff.fingerprint(ws, False, False, 'image')
        with patch.object(handoff, 'STYLING_PROMPT_VERSION', handoff.STYLING_PROMPT_VERSION + 1):
            self.assertNotEqual(old, handoff.fingerprint(ws, False, False, 'image'))
