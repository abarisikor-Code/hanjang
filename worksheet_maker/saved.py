"""저장한 HTML 안에 학습지 구조를 함께 담고, 다시 꺼낸다.

HTML 파일 하나로 인쇄도 하고, 앱에 다시 올려 편집도 할 수 있게 하기 위한 것.
구조는 <script type="application/json" id="hanjang-data"> 안에 들어간다(브라우저는 실행하지 않는다).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from markupsafe import Markup
from pydantic import ValidationError

from .forms import Form
from .schema import Worksheet
from .theme import Theme

FORMAT_VERSION = 3  # 2: 내 학습지 양식(form) 추가, 3: 한글 양식(form.doc). 예전 파일도 그대로 열린다.
_DATA_TAG = re.compile(
    r'<script[^>]*\bid=["\']hanjang-data["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)


class LoadError(Exception):
    """사용자에게 그대로 보여줄 수 있는 불러오기 실패 메시지."""


@dataclass
class Saved:
    worksheet: Worksheet
    theme: Theme | None = None
    level: str | None = None
    footer: str | None = None
    show_answers: bool | None = None
    form: Form | None = None


def pack(ws: Worksheet, theme: Theme, level: str, footer: str, show_answers: bool = False,
         form: Form | None = None) -> Markup:
    """HTML <script> 안에 넣을 JSON. '<'를 이스케이프해 </script>로 끊기지 않게 한다."""
    data = {
        "app": "hanjang",
        "version": FORMAT_VERSION,
        "level": level,
        "footer": footer,
        "show_answers": show_answers,
        "theme": theme.model_dump(),
        "worksheet": ws.model_dump(),
        "form": form.model_dump() if form else None,
    }
    text = json.dumps(data, ensure_ascii=False, indent=1)
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def unpack(filename: str, data: bytes) -> Saved:
    """한장으로 저장한 HTML(또는 구조 JSON)을 읽어 편집 상태로 되돌린다."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise LoadError("파일을 읽을 수 없습니다. 한장에서 저장한 HTML 파일인지 확인해 주세요.") from e

    if Path(filename).suffix.lower() in (".html", ".htm"):
        m = _DATA_TAG.search(text)
        if not m:
            raise LoadError("이 HTML에는 편집 정보가 없습니다. 한장에서 'HTML 저장'으로 만든 파일만 다시 열 수 있어요.")
        raw = m.group(1)
    else:
        raw = text

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LoadError(f"파일 안의 학습지 정보가 손상되었습니다. ({e.msg})") from e

    try:
        if isinstance(obj, dict) and "worksheet" in obj:  # 한장 저장 형식
            theme, form = obj.get("theme"), obj.get("form")
            return Saved(
                worksheet=Worksheet.model_validate(obj["worksheet"]),
                theme=Theme.model_validate(theme) if isinstance(theme, dict) else None,
                level=obj.get("level") if isinstance(obj.get("level"), str) else None,
                footer=obj.get("footer") if isinstance(obj.get("footer"), str) else None,
                show_answers=obj.get("show_answers") if isinstance(obj.get("show_answers"), bool) else None,
                form=Form.model_validate(form) if isinstance(form, dict) else None,
            )
        return Saved(worksheet=Worksheet.model_validate(obj))  # 예전 '구조(JSON) 저장' 파일
    except ValidationError as e:
        raise LoadError(f"학습지 정보의 형식이 올바르지 않습니다.\n{e}") from e
