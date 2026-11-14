"""초안만 medium, 필수 검토·수정과 그 밖 요청은 high로 보낸다."""
import json
import unittest
from unittest.mock import patch
import httpx
from worksheet_maker import llm, diagnostics
from worksheet_maker.schema import Worksheet, Block
from repair_fixtures import worksheet_patch


class DraftThinkingTests(unittest.TestCase):
    def test_real_sdk_draft_recovery_medium_then_review_repair_high(self):
        ws = Worksheet(kind='inquiry', title='가상 융합', blocks=[
            Block(type='short_answer', body='3 + 4를 계산해 보세요.', answer='7', solution='3+4=7')])
        issue = dict(category='answer', question=1, evidence='정답 검산 필요', fix='정답 검산')
        replies = [None, ws.model_dump_json(), json.dumps({'issues': [issue]}),
                   json.dumps(worksheet_patch(ws)), '{"issues": []}']
        levels = []
        def send(request, **kwargs):
            payload = json.loads(request.content)
            config = payload['generationConfig']['thinkingConfig']
            levels.append((config.get('thinkingLevel') or config['thinking_level']).lower())
            reply = replies[len(levels)-1]
            if reply is None:
                return httpx.Response(503, request=request, json={'error': {'code':503,'message':'busy'}})
            return httpx.Response(200, request=request,
                json={'candidates':[{'content':{'parts':[{'text':reply}]}}]})
        client = llm._generation_client('offline-test-key')
        try:
            with patch('httpx.Client.send', side_effect=send), patch.object(llm.time, 'sleep'), diagnostics.capture('1.5.6') as report:
                llm._generate_for_request(client, llm.MODELS[0], '가상 조건', request='', context='4학년',
                    n_questions=1, kind='inquiry', task_type='story', subjects=['수학','과학'])
        finally:
            client.close()
        self.assertEqual(levels, ['medium','medium','high','high','high'])
        settings = [e['thinking'] for e in report['events'] if e['event']=='request_settings']
        self.assertEqual(settings, levels)
        self.assertIsNone(llm._thinking_level.get())

    def test_failure_restores_high_for_next_request(self):
        class Busy(Exception): code = 503
        with patch.object(llm, '_generate', side_effect=Busy()), patch.object(llm.time, 'sleep'):
            with self.assertRaises(llm.AnalyzeError):
                llm._generate_for_request(None,llm.MODELS[0],'가상',request='',context='4학년',n_questions=1,
                    kind='inquiry',task_type='story',subjects=['수학','과학'])
        self.assertIsNone(llm._thinking_level.get())
        self.assertEqual(llm.THINKING, 'high')


if __name__ == '__main__': unittest.main()
