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

    def test_missing_scenes_are_advisory_and_do_not_trigger_extra_calls(self):
        ws = self.worksheet()
        outputs = [ws.model_dump_json(), '{"issues": []}']
        with patch.object(llm, '_generate', side_effect=outputs) as generate:
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '이야기 만들기', request='',
                context='4학년', n_questions=4, kind='inquiry', task_type='story', subjects=['수학', '과학'])
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(Worksheet.model_validate_json(raw), ws)
        self.assertIn('후속 장면', ' '.join(llm.LAST_GENERATION_NOTES.get()))

    def test_mixed_findings_repair_only_required_errors_and_recheck(self):
        ws = self.worksheet()
        soft = dict(category='linkage', question=0, evidence='사건과 문항의 연결이 부족함', fix='연결 개선')
        hard = dict(category='answer', question=1, evidence='정답이 틀림', fix='정답 수정')
        outputs = [ws.model_dump_json(), json.dumps({'issues': [soft, hard]}),
                   json.dumps(worksheet_patch(ws)), json.dumps({'issues': [soft]})]
        with patch.object(llm, '_generate', side_effect=outputs) as generate:
            llm._generate_for_request(None, llm.MODELS[0], '이야기 만들기', request='',
                context='4학년', n_questions=4, kind='inquiry', task_type='story', subjects=['수학', '과학'])
        self.assertEqual(generate.call_count, 4)
        repair = generate.call_args_list[2].args[2]
        self.assertIn('정답이 틀림', repair)
        self.assertNotIn('사건과 문항의 연결이 부족함', repair)

    def test_old_connection_only_checkpoint_completes_without_more_calls(self):
        ws = self.worksheet()
        state = dict(mode='worksheet', draft=ws.model_dump(), kind='inquiry', n_questions=4,
            used=llm.MODELS[0], task_type='story', subjects=['수학', '과학'], attempt=2,
            phase='repair', prompt='', request='', context='',
            issues=[dict(category='repetition', question=1, evidence='유사 문제 반복', fix='다양화')])
        with patch.object(llm, '_generate') as generate:
            raw, _ = llm._review_saved_draft(None, state)
        generate.assert_not_called()
        self.assertEqual(Worksheet.model_validate_json(raw), ws)
        self.assertEqual(llm.LAST_GENERATION_NOTES.get(), ('1번: 유사 문제 반복',))
        state.update(phase='review', task_type=None, issues=[])
        with patch.object(llm, '_generate', return_value='{"issues": []}'):
            llm._review_saved_draft(None, state)
        self.assertFalse(llm.LAST_GENERATION_NOTES.get())


if __name__ == '__main__':
    unittest.main()
