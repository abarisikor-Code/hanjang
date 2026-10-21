"""관심사 지시가 각 생성 경로에 전달되는지 외부 호출 없이 확인한다."""
import json
import unittest
from unittest.mock import patch

from worksheet_maker import curriculum, llm


class InterestPromptTests(unittest.TestCase):
    def capture(self, generate, **kwargs):
        raw = json.dumps({'title': '시험 학습지', 'blocks': []})
        with patch('google.genai.Client'), patch.object(llm, '_generate_for_request', return_value=(raw, '시험 모델')) as call:
            generate(api_key='시험용', **kwargs)
        return call.call_args.args[2]

    def test_single_with_interest_and_without_extra(self):
        args = dict(grade=3, subject='수학', standards=curriculum.find(['4수01-04']))
        prompt = self.capture(llm.generate_for_standards, **args, extra='공룡을 좋아해')
        self.assertIn('공룡을 좋아해', prompt)
        self.assertIn('관심사·주제를 배울 내용과 엮는 법', prompt)
        self.assertNotIn('관심사·주제를 배울 내용과 엮는 법', self.capture(llm.generate_for_standards, **args))

    def test_integrated_theme_and_separate_subjects(self):
        prompt = self.capture(llm.generate_integrated, grade=3,
            picks={'수학': curriculum.find(['4수01-04']), '국어': curriculum.find(['4국02-02'])},
            candidates={}, theme='축구를 좋아해', task_type='separate')
        self.assertIn('축구를 좋아해', prompt)
        self.assertIn('관심사·주제를 배울 내용과 엮는 법', prompt)
        self.assertIn('과목 사이를 억지로 엮지 않는다', prompt)

    def test_separate_subjects_keep_the_original_generation_path(self):
        with patch('google.genai.Client'), patch.object(llm, '_generate_for_request',
                return_value=(json.dumps({'title': '과목별 학습지', 'blocks': []}), '시험 모델')) as generate:
            llm.generate_integrated(api_key='시험용', grade=3, picks={'수학': [], '국어': []},
                candidates={'수학': [], '국어': []}, theme='공룡', task_type='separate')
        self.assertFalse(generate.call_args.kwargs['plan_activity'])

    def test_integrated_extra_without_theme(self):
        prompt = self.capture(llm.generate_integrated, grade=3,
            picks={'수학': curriculum.find(['4수01-04']), '국어': curriculum.find(['4국02-02'])},
            candidates={}, extra='고양이를 좋아해')
        self.assertIn('관심사·주제를 배울 내용과 엮는 법', prompt)

    def test_outline_uses_title_interest(self):
        prompt = self.capture(llm.generate_from_outline, title='공룡 박물관', goals=['곱셈을 해요'])
        self.assertIn('관심사·주제를 배울 내용과 엮는 법', prompt)

    def test_drill_and_basic_constraints_remain(self):
        prompt = self.capture(llm.generate_for_standards, grade=1, subject='수학',
            standards=curriculum.find(['2수01-06']), extra='공룡을 좋아해', difficulty='basic', kind='drill')
        self.assertIn('반복 연습지다', prompt)
        self.assertIn('한 문항에 한 가지 짧은 행동', prompt)
        self.assertIn('조건만 있으면 주제를 새로 강요하지 않는다', prompt)
        self.assertIn('학년 범위 (가장 중요', prompt)

    def test_request_parser_keeps_activity_in_instructions(self):
        with patch('google.genai.Client') as client:
            client.return_value.interactions.create.return_value.output_text = json.dumps({
                'grade': 3, 'subjects': ['수학'], 'codes': ['4수01-04'],
                'kind': 'standard', 'task_type': 'separate', 'extra': '공룡을 좋아하고 박물관을 꾸미고 싶어',
            })
            result = llm.understand_request(api_key='시험용',
                text='3학년 수학 곱셈. 공룡을 좋아하고 박물관을 꾸미고 싶어', catalog='시험 목록', default_grade=3)
        prompt = client.return_value.interactions.create.call_args.kwargs['input']
        self.assertIn('그대로 남긴다', prompt)
        self.assertIn('관심사 때문에 과목·성취기준·난이도·과제 형태를 임의로 바꾸지 않는다', prompt)
        self.assertEqual(result['extra'], '공룡을 좋아하고 박물관을 꾸미고 싶어')

    def test_all_generation_paths_pass_context_to_planner(self):
        cases = [
            (llm.generate_for_standards, dict(grade=3, subject='수학',
                standards=curriculum.find(['4수01-04']), extra='공룡을 좋아해')),
            (llm.generate_integrated, dict(grade=3, picks={'수학': [], '국어': []},
                candidates={'수학': [], '국어': []}, theme='공룡을 좋아해')),
            (llm.generate_from_outline, dict(title='공룡 박물관')),
        ]
        payload = {'title': '나의 박물관', 'blocks': []}
        for generate, kwargs in cases:
            with self.subTest(path=generate.__name__), patch('google.genai.Client'), patch.object(
                    llm, '_generate_for_request', return_value=(json.dumps(payload), '시험 모델')) as call:
                result = generate(api_key='시험용', **kwargs)
                self.assertIn('공룡', call.call_args.kwargs['request'])
                self.assertTrue(call.call_args.kwargs['context'])
                self.assertEqual(call.call_args.kwargs['n_questions'], 8)
                self.assertEqual(result.worksheet.title, '나의 박물관')
                self.assertNotIn('interest_plan', result.worksheet.model_dump())

    def test_no_request_and_other_kinds_keep_original_generator(self):
        for request, kind in [('', 'standard'), ('공룡을 좋아해', 'drill'), ('공룡을 좋아해', 'note'), ('공룡을 좋아해', 'game')]:
            with self.subTest(kind=kind), patch.object(llm, '_generate_with_retry', return_value=('원래 응답', '기존 모델')) as old, patch.object(llm, '_generate') as direct:
                result = llm._generate_for_request(None, llm.MODELS[0], '기존 지시', request=request,
                    context='학년 범위', n_questions=4, kind=kind)
                self.assertEqual(result, ('원래 응답', '기존 모델'))
                old.assert_called_once()
                direct.assert_not_called()


if __name__ == '__main__':
    unittest.main()
