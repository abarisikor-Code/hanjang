"""행성 융합에서 발견된 범위 해설 누락과 이야기 장면 누락을 재현한다."""
import unittest
from unittest.mock import patch

from worksheet_maker import content_review, curriculum, llm
from worksheet_maker.schema import Block, Worksheet
from repair_fixtures import worksheet_patch
import json


class StoryScopeTests(unittest.TestCase):
    def test_selected_science_exclusions_reach_generation_and_review_context(self):
        selected = curriculum.find(['4과13-02'])
        explanation = selected[0].explain
        self.assertIn('구체적인 물리량은 다루지 않는다', explanation)
        with patch.object(llm, '_generation_client'), patch.object(llm, '_generate_for_request',
                return_value=(Worksheet(title='범위 전달 검사').model_dump_json(), llm.MODELS[0])) as generate:
            llm.generate_integrated(api_key='offline-test-key', grade=4,
                picks={'수학': curriculum.find(['4수01-02']), '과학': selected},
                candidates={}, task_type='story', n_questions=11)
        self.assertIn(explanation, generate.call_args.args[2])
        self.assertIn(explanation, generate.call_args.kwargs['context'])

    def worksheet(self):
        return Worksheet(title='안내판을 찾아라', kind='inquiry', blocks=[
            Block(type='reading', body='전시관의 안내판이 뒤바뀌었습니다.'),
            *[Block(type='short_answer', body='어떤 안내판이 맞나요?', answer='가') for _ in range(4)]])

    def test_intro_and_independent_questions_are_not_a_story(self):
        ws = self.worksheet()
        self.assertTrue(content_review.structure_issues(ws, 'inquiry', 4, task_type='story'))
        for task in ('project', 'inquiry', 'separate', None):
            self.assertFalse(content_review.structure_issues(ws, 'inquiry', 4, task_type=task))
        ws.blocks.insert(3, Block(type='section', title='다음 장면'))
        self.assertTrue(content_review.structure_issues(ws, 'inquiry', 4, task_type='story'))
        ws.blocks.insert(4, Block(type='reading', body='찾은 단서를 따라 두 번째 전시실로 갔습니다.'))
        self.assertFalse(content_review.structure_issues(ws, 'inquiry', 4, task_type='story'))

    def test_empty_ai_reviews_cannot_release_story_without_later_scenes(self):
        ws = self.worksheet()
        outputs = [ws.model_dump_json(), '{"issues": []}', json.dumps(worksheet_patch(ws)),
                   '{"issues": []}', json.dumps(worksheet_patch(ws)), '{"issues": []}']
        with patch.object(llm, '_generate', side_effect=outputs) as generate:
            with self.assertRaises(llm.AnalyzeError):
                llm._generate_for_request(None, llm.MODELS[0], '이야기 만들기', request='',
                    context='4학년', n_questions=4, kind='inquiry', task_type='story', subjects=['수학', '과학'])
        self.assertEqual(generate.call_count, 6)


if __name__ == '__main__':
    unittest.main()
