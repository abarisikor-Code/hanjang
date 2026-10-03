"""내 학습지 양식: 사용자가 올린 학습지(PDF·사진)를 종이 배경으로 깔고, 정해진 칸 안에 문제를 넣는다.

AI는 칸의 위치(제목 칸·문제 칸, 원래 글을 지울지)만 정하고, 문제 배치는 render가 만든 HTML 안의 코드가 한다.
칸 좌표는 쪽 그림 전체를 0~1000으로 본 [ymin, xmin, ymax, xmax] (따라 만들기의 그림 자르기와 같은 방식).
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .docform import DocForm

FORM_DIR = Path(__file__).resolve().parent.parent / "forms"
MAX_PAGES = 4
MAX_SIDE = 2000  # 긴 변 2000px ≈ A4 세로 170dpi: 인쇄에 충분하고 파일은 너무 크지 않게
A4_W, A4_H = 210.0, 297.0  # mm

REGION_KINDS = {"content": "문제 칸", "title": "제목 칸"}


class FormError(Exception):
    """사용자에게 그대로 보여줄 수 있는 메시지."""


class Region(BaseModel):
    kind: Literal["content", "title"] = "content"
    box: list[int] = Field(default_factory=lambda: [100, 50, 950, 950])  # [ymin, xmin, ymax, xmax], 0~1000
    erase: bool = False  # 칸 안에 원래 인쇄된 글(예전 문제·제목)을 흰색으로 덮는다

    @field_validator("box", mode="before")
    @classmethod
    def _clean_box(cls, v: object) -> list[int]:
        try:
            y0, x0, y1, x1 = (max(0, min(1000, round(float(n)))) for n in v)  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return [100, 50, 950, 950]
        y0, y1 = sorted((y0, y1))
        x0, x1 = sorted((x0, x1))
        return [y0, x0, max(y1, y0 + 10), max(x1, x0 + 10)]


class FormPage(BaseModel):
    image: str  # data:image/jpeg;base64,…
    width: int
    height: int
    regions: list[Region] = Field(default_factory=list)

    @field_validator("image")
    @classmethod
    def _safe_image(cls, v: str) -> str:
        if not re.fullmatch(r"data:image/(?:png|jpeg);base64,[A-Za-z0-9+/]+=*", v or ""):
            raise ValueError("양식 그림 형식이 올바르지 않습니다.")
        return v

    def canvas_mm(self) -> tuple[float, float, float, float]:
        """A4 종이 안에 쪽 그림을 비율 그대로 꽉 채웠을 때의 (left, top, width, height) mm."""
        ratio = self.width / self.height if self.height else A4_W / A4_H
        if ratio >= A4_W / A4_H:
            w, h = A4_W, A4_W / ratio
        else:
            w, h = A4_H * ratio, A4_H
        return round((A4_W - w) / 2, 2), round((A4_H - h) / 2, 2), round(w, 2), round(h, 2)


class Form(BaseModel):
    name: str = "내 양식"
    pages: list[FormPage] = Field(default_factory=list)  # PDF·사진 양식: 쪽 그림 위의 칸
    doc: DocForm | None = None  # 한글(HWPX) 양식: 파일의 짜임을 그대로 HTML로

    def n_regions(self) -> int:
        if self.doc:
            return 1
        return sum(1 for p in self.pages for r in p.regions if r.kind == "content")


def _encode(img) -> tuple[str, int, int]:
    from PIL import Image

    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return f"data:image/jpeg;base64,{base64.b64encode(out.getvalue()).decode('ascii')}", img.width, img.height


def pages_from_upload(data: bytes, mime: str) -> list[FormPage]:
    """올린 PDF·사진을 양식 쪽 그림으로 바꾼다 (PDF는 앞 4쪽까지). 칸은 아직 없다."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    pages = []
    try:
        if mime == "application/pdf":
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(data)
            for i in range(min(len(pdf), MAX_PAGES)):
                pdf_page = pdf[i]
                scale = MAX_SIDE / max(pdf_page.get_size())  # 긴 변이 MAX_SIDE가 되게
                uri, w, h = _encode(pdf_page.render(scale=scale).to_pil())
                pages.append(FormPage(image=uri, width=w, height=h))
        else:
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
            uri, w, h = _encode(img)
            pages.append(FormPage(image=uri, width=w, height=h))
    except (UnidentifiedImageError, OSError, ValueError) as e:
        raise FormError("양식 파일을 읽을 수 없어요. PDF나 PNG·JPG 그림인지 확인해 주세요.") from e
    except Exception as e:  # pypdfium2의 손상된 PDF 등
        raise FormError(f"양식 파일을 읽을 수 없어요. ({e})") from e
    if not pages:
        raise FormError("양식 파일에 쪽이 없어요.")
    return pages


def hwp_preview(data: bytes) -> bytes:
    """옛 한글(HWP) 파일 안에 든 첫 쪽 미리보기 그림 (본문은 읽지 않는다)."""
    try:
        import olefile

        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            return ole.openstream("PrvImage").read()
    except Exception as e:
        raise FormError("HWP 파일을 읽을 수 없어요. 한글에서 '다른 이름으로 저장 → HWPX'로 저장해 올려 주세요.") from e


def snap(page: FormPage) -> None:
    """AI가 잡은 칸이 양식의 테두리 선에 걸치면 선 안쪽으로 당긴다.
    (칸을 흰색으로 지울 때 테두리까지 지워지거나, 글이 선 위에 겹치지 않도록)"""
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(page_bytes(page))).convert("L")
    img.thumbnail((1000, 1000))
    dark = np.asarray(img) < 140
    h, w = dark.shape
    pad_y, pad_x = max(2, int(0.003 * h)), max(2, int(0.003 * w))  # 선 바로 안쪽까지 (글과 선 사이 여백은 CSS가 둔다)

    def lines(lo: int, hi: int, along) -> list[int]:  # 칸 폭(높이)의 60% 넘게 이어진 선의 위치
        return [i for i in range(max(lo, 0), min(hi, along.shape[0])) if along[i].mean() > 0.6]

    for r in page.regions:
        y0, x0, y1, x1 = (int(r.box[0] * h / 1000), int(r.box[1] * w / 1000),
                          int(r.box[2] * h / 1000), int(r.box[3] * w / 1000))
        rows, cols = dark[:, x0:x1], dark[y0:y1, :].T
        # 칸 끝 근처만 본다 (작은 칸은 맞은편 테두리를 잘못 잡지 않게 칸 크기의 30%까지만)
        band_y, band_x = int(min(0.04 * h, 0.3 * (y1 - y0))), int(min(0.04 * w, 0.3 * (x1 - x0)))
        top, bottom = lines(y0 - band_y, y0 + band_y, rows), lines(y1 - band_y, y1 + band_y, rows)
        left, right = lines(x0 - band_x, x0 + band_x, cols), lines(x1 - band_x, x1 + band_x, cols)
        ny0 = max(top) + pad_y if top else y0
        ny1 = min(bottom) - pad_y if bottom else y1
        nx0 = max(left) + pad_x if left else x0
        nx1 = min(right) - pad_x if right else x1
        if ny1 - ny0 > max(0.012 * h, 0.35 * (y1 - y0)) and nx1 - nx0 > 0.35 * (x1 - x0):  # 너무 작아지면 AI 값을 그대로 둔다
            r.box = Region._clean_box([ny0 * 1000 / h, nx0 * 1000 / w, ny1 * 1000 / h, nx1 * 1000 / w])


def page_bytes(page: FormPage) -> bytes:
    return base64.b64decode(page.image.split(",", 1)[1])


def preview(page: FormPage) -> bytes:
    """칸 위치를 확인하는 그림: 문제 칸은 빨강, 제목 칸은 파랑 테두리에 번호를 붙인다."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.open(io.BytesIO(page_bytes(page))).convert("RGB")
    img.thumbnail((900, 900))
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:  # 오래된 Pillow
        font = ImageFont.load_default()
    n = 0
    for r in page.regions:
        y0, x0, y1, x1 = r.box
        rect = (x0 * w / 1000, y0 * h / 1000, x1 * w / 1000, y1 * h / 1000)
        color = (220, 30, 30) if r.kind == "content" else (30, 90, 220)
        draw.rectangle(rect, outline=color + (255,), width=4, fill=color + (28,))
        n += 1
        draw.rectangle((rect[0], rect[1], rect[0] + 34, rect[1] + 30), fill=color + (255,))
        draw.text((rect[0] + 9, rect[1] + 3), str(n), fill=(255, 255, 255), font=font)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ----- 저장해 둔 양식 (forms/*.json) -----
def _path(name: str) -> Path:
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "내 양식"
    return FORM_DIR / f"{safe}.json"


def save_form(form: Form) -> Path:
    FORM_DIR.mkdir(exist_ok=True)
    p = _path(form.name)
    p.write_text(form.model_dump_json(), encoding="utf-8")
    return p


def list_forms() -> list[str]:
    return sorted(p.stem for p in FORM_DIR.glob("*.json")) if FORM_DIR.exists() else []


def load_form(name: str) -> Form:
    try:
        return Form.model_validate(json.loads(_path(name).read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        raise FormError(f"저장한 양식 '{name}'을 열 수 없어요.") from e


def delete_form(name: str) -> None:
    _path(name).unlink(missing_ok=True)
