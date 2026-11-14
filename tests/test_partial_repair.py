import copy
import json
import unittest
from unittest.mock import patch
import httpx

from worksheet_maker import partial_repair, content_review, llm, activity
from worksheet_maker.schema import Worksheet, Block
from test_activity_design import sample_plan


class PartialRepairTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(title='큰 수와 곱셈', kind='standard', blocks=[
            Block(type='concept', body='같은 자릿수끼리 계산해요.'),
            Block(type='table', rows=[['상자 수', '한 상자의 책'], ['12', '24']]),
            *[Block(type='short_answer', body=f'{n} × 24를 계산해 보세요.', answer=str(n*24), solution='곱셈') for n in range(1, 11)]])

    def test_only_selected_blocks_change_and_response_is_smaller(self):
        ws = self.worksheet()
        original = ws.model_dump()
        fixed = copy.deepcopy(original['blocks'][4])
        fixed['answer'] = '72'
        delta = {'header': {}, 'block_edits': [{'at': 4, 'delete': 1, 'values': [fixed]}]}
        before = copy.deepcopy(delta)
        result = partial_repair.apply(original, delta, 'worksheet')
        self.assertEqual(result['blocks'][4], fixed)
        for index, block in enumerate(original['blocks']):
            if index != 4:
                self.assertEqual(result['blocks'][index], block)
        self.assertEqual(original, ws.model_dump())
        self.assertEqual(delta, before)
        self.assertLess(len(json.dumps(delta)), len(ws.model_dump_json()) // 2)
        self.assertEqual(content_review.question_count(Worksheet.model_validate(result)), 10)

    def test_missing_example_can_be_inserted_without_rewriting_questions(self):
        ws = Worksheet(title='개념', blocks=[Block(type='short_answer', body='23 + 14는?', answer='37')])
        delta = {'header': {}, 'block_edits': [{'at': 0, 'delete': 0,
            'values': [Block(type='concept', body='풀이 예시: 12 + 13 = 25입니다.').model_dump()]}]}
        fixed = Worksheet.model_validate(partial_repair.apply(ws.model_dump(), delta, 'worksheet'))
        self.assertEqual(fixed.blocks[1], ws.blocks[0])
        self.assertEqual(content_review.structure_issues(fixed, 'standard', 1), [])

    def test_related_activity_material_and_question_are_repaired_together(self):
        plan = sample_plan()
        original = copy.deepcopy(plan)
        material = copy.deepcopy(plan['materials'][0])
        material['rows'][2] = ['나', '24', '2']
        question = copy.deepcopy(plan['questions'][1])
        question.update(answer='한 줄에 24장씩 2줄입니다.', solution='24×2=48')
        delta = {'header': {}, 'material_edits': [{'at':0, 'delete':1, 'values':[material]}],
                 'question_edits': [{'at':1, 'delete':1, 'values':[question]}]}
        fixed = partial_repair.apply(plan, delta, 'activity')
        self.assertEqual(fixed['questions'][0], plan['questions'][0])
        self.assertEqual(fixed['purpose'], plan['purpose'])
        self.assertEqual(fixed['materials'][0], material)
        self.assertEqual(plan, original)
        self.assertIsNotNone(activity.to_worksheet(fixed, 2, kind='inquiry'))

    def test_invalid_or_overlapping_locations_do_not_touch_original(self):
        original = self.worksheet().model_dump()
        before = copy.deepcopy(original)
        for edits in ([{'at': -1, 'delete': 1, 'values': []}],
                      [{'at': True, 'delete': 1, 'values': []}],
                      [{'at': 999, 'delete': 0, 'values': []}],
                      [{'at': 11, 'delete': 2, 'values': []}],
                      [{'at': 1, 'delete': 2, 'values': []}, {'at': 2, 'delete': 1, 'values': []}],
                      [{'at': 1, 'delete': 0, 'values': []}, {'at': 1, 'delete': 0, 'values': []}]):
            with self.subTest(edits=edits), self.assertRaises(ValueError):
                partial_repair.apply(original, {'header': {}, 'block_edits': edits}, 'worksheet')
            self.assertEqual(original, before)
        with self.assertRaises(ValueError):
            partial_repair.apply(original, {'header': {'kind':'drill'}, 'block_edits': []}, 'worksheet')

    def test_offsets_use_original_array_order_and_unknown_fields_are_rejected(self):
        original = self.worksheet().model_dump()
        delta = {'header': {}, 'block_edits': [
            {'at': 5, 'delete': 1, 'values': []},
            {'at': 1, 'delete': 0, 'values': [Block(type='text', body='설명').model_dump()]}]}
        result = partial_repair.apply(original, delta, 'worksheet')
        self.assertEqual(result['blocks'][6], original['blocks'][6])
        self.assertEqual(result['blocks'][2], original['blocks'][1])
        with self.assertRaises(ValueError):
            partial_repair.apply(original, dict(delta, worksheet=original), 'worksheet')

    def test_changed_answer_is_still_subject_to_full_review_and_repair_limit(self):
        ws = Worksheet(title='가상', kind='standard', blocks=[Block(type='concept', body='같은 자릿수끼리 더해요.'),
            Block(type='short_answer', body='23 + 14를 계산해 보세요.', answer='38', solution='계산')])
        fixed = ws.blocks[1].model_copy(update={'answer':'37', 'solution':'23+14=37'})
        delta = {'header': {}, 'block_edits':[{'at':1, 'delete':1, 'values':[fixed.model_dump()]}]}
        issue = dict(category='answer', question=1, evidence='정답 오류', fix='37로 검산')
        with patch.object(llm, '_generate', side_effect=[ws.model_dump_json(), json.dumps({'issues':[issue]}),
                json.dumps(delta), '{"issues":[]}']) as generate:
            raw, _ = llm._generate_for_request(None, llm.MODELS[0], '지시', request='', context='3학년', n_questions=1)
        self.assertEqual(generate.call_count, 4)
        self.assertIn('block_edits', generate.call_args_list[2].args[4]['properties'])
        self.assertIn('issues', generate.call_args_list[3].args[4]['properties'])
        self.assertEqual(Worksheet.model_validate_json(raw).blocks[1].answer, '37')
        self.assertIn('"answer": "37"', generate.call_args_list[3].args[2])

    def test_prompt_distinguishes_printed_question_number_from_block_offset(self):
        ws = self.worksheet()
        instruction = partial_repair.prompt('원래 수업 조건', ws.model_dump(), [], 'worksheet')
        data = json.loads(instruction.split('배열 위치를 표시한 원본:\n')[1].split('\n\n검토 근거')[0])
        self.assertEqual(data['blocks'][2]['at'], 2)
        self.assertEqual(data['blocks'][2]['question_numbers'], [1])

    def test_sdk_serializes_both_patch_schemas_without_hidden_retries(self):
        for mode, result in [('worksheet', {'header': {}, 'block_edits': []}),
                             ('activity', {'header': {}, 'question_edits': [], 'material_edits': []})]:
            sent = []
            def send(request, **kwargs):
                sent.append(json.loads(request.content))
                return httpx.Response(200, request=request, json={'candidates': [{'content': {
                    'parts': [{'text': json.dumps(result)}]}}]})
            client = llm._generation_client('offline-test-key')
            try:
                with self.subTest(mode=mode), patch('httpx.Client.send', side_effect=send), llm.generation_budget():
                    raw = llm._generate(client, llm.MODELS[0], '가상 부분 수정', [], partial_repair.schema(mode))
            finally:
                client.close()
            self.assertEqual(len(sent), 1)
            self.assertEqual(json.loads(raw), result)
            self.assertEqual(sent[0]['generationConfig']['responseJsonSchema'], partial_repair.schema(mode))
