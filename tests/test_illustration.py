import base64
import json
import re
import unittest
from unittest.mock import patch

from worksheet_maker import illustration, llm
from worksheet_maker.forms import Form
from worksheet_maker.render import render
from worksheet_maker.saved import unpack
from worksheet_maker.schema import Block, Worksheet


class IllustrationTests(unittest.TestCase):
    def worksheet(self):
        return Worksheet(title='달 관찰과 분수', blocks=[
            Block(type='reading', body='4일 중 1일과 2일을 관찰합니다.'),
            Block(type='table', rows=[['요일', '무게'], ['월요일', '1.25 kg']]),
            Block(type='diagram', diagram={'kind': 'number_line', 'start': 0, 'end': 4, 'step': 1}),
            Block(type='multiple_choice', body='남은 기간은 얼마인가요?', items=['1/4', '3/4'], answer='1/4'),
            Block(type='fill_blank', body='달은 [[태양]] 빛을 받습니다.'),
            Block(type='ox', items=['달은 스스로 빛을 냅니다.', '달은 매일 조금씩 달라집니다.'], answer='X, O'),
            Block(type='essay', body='관찰 계획을 쓰세요.', lines=4, answer='자유롭게', solution='계획 확인'),
            Block(type='drill', items=['1/4 + 2/4 = [[3/4]]'])])

    def decoration(self, **kwargs):
        return illustration.Decoration(image='data:image/png;base64,' + base64.b64encode(b'test').decode(), **kwargs)

    def test_decorating_preserves_entire_question_markup_and_roundtrip(self):
        ws = self.worksheet()
        before = ws.model_dump_json()
        plain = render(ws, show_answers=True)
        for answers in (False, True):
            art = self.decoration(answers=answers)
            decorated = render(ws, show_answers=True, decoration=art)
            # 문제·번호·자료·도식·하위 문항·빈칸의 HTML 전체가 그대로여야 한다.
            body = lambda html: re.search(r'<div class="ws-body">(.*?)</main>', html, re.S).group(1)
            self.assertEqual(body(plain), body(decorated))
            self.assertIn('<svg', body(decorated))
            self.assertEqual(re.findall(r'<span class="ak-num">.*?</li>', plain, re.S),
                             re.findall(r'<span class="ak-num">.*?</li>', decorated, re.S))
            saved = unpack('학습지.html', decorated.encode())
            self.assertEqual(saved.worksheet, ws)
            self.assertEqual(saved.decoration, art)
            self.assertEqual(ws.model_dump_json(), before)
            self.assertIn('answer-sheet illustrated' if answers else 'class="sheet answer-sheet"', decorated)

    def test_saved_older_files_still_load_and_unsafe_assets_are_blocked(self):
        old = {'worksheet': self.worksheet().model_dump(), 'version': 3}
        self.assertIsNone(unpack('old.json', json.dumps(old).encode()).decoration)
        for image in ('https://example.com/a.png', 'javascript:alert(1)', 'data:image/svg+xml;base64,abc'):
            self.assertEqual(illustration.Decoration(image=image).image, '')
        art = illustration.Decoration(palette='night" onclick="bad', size='300mm')
        self.assertEqual(art.palette, 'night')
        self.assertEqual(art.size, 'medium')

    def test_form_and_thumbnail_keep_existing_layout(self):
        # 빈 양식에서도 기존 우선순위를 지켜 장식 삽화를 겹치지 않는다.
        html = render(self.worksheet(), form=Form(), decoration=self.decoration())
        self.assertNotIn('class="illustration-hero', html)
        self.assertEqual(unpack('양식.html', html.encode()).decoration, self.decoration())
        self.assertNotIn('class="illustration-hero', render(self.worksheet(), thumbnail=True, decoration=self.decoration()))

    def test_illustration_prompt_excludes_teacher_answers_and_never_requests_full_worksheet(self):
        ws = self.worksheet()
        request = illustration.prompt(ws, '달빛 천문대')
        self.assertIn('달빛 천문대', request)
        self.assertIn('대표 삽화 한 장', request)
        self.assertIn('글자·숫자·제목', request)
        self.assertNotIn('계획 확인', request)
        self.assertNotIn('태양', request)
        with patch.object(llm, '_generation_client'), patch.object(llm, '_generate', return_value=json.dumps({
                'request': '보라와 파랑의 밤하늘에 망원경을 보는 캐릭터를 그려 주세요.'})) as call:
            result = llm.suggest_styling_prompt(api_key='가짜 키', worksheet=ws, output='illustration')
        self.assertIn('글자 없는 그림만', call.call_args.args[2])
        self.assertIn('페이지 배치나 문제를 다시 작성하지 마세요', result)
        self.assertEqual(call.call_count, 1)
