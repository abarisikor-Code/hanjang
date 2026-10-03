"""그림 파일 → 학습지 HTML 안에 넣을 data URI.

A4 인쇄에 충분한 크기(긴 변 1600px)로 줄이고, 사진은 JPEG·선 그림은 PNG 중 더 작은 쪽으로 저장해
HTML 파일 하나에 그림까지 담기도록 한다.
"""

from __future__ import annotations

import base64
import io

MAX_SIDE = 1600
MAX_UPLOAD = 15 * 1024 * 1024
ACCEPT = ["png", "jpg", "jpeg", "webp", "gif"]


class ImageError(Exception):
    pass


def to_data_uri(data: bytes) -> str:
    from PIL import Image, ImageOps, UnidentifiedImageError

    if len(data) > MAX_UPLOAD:
        raise ImageError("그림 파일이 너무 큽니다(15MB 이하).")
    try:
        im = Image.open(io.BytesIO(data))
        original = (im.format or "").lower(), im.size
        im = ImageOps.exif_transpose(im)  # 휴대폰 사진 회전 바로잡기
        im.thumbnail((MAX_SIDE, MAX_SIDE))
    except (UnidentifiedImageError, OSError) as e:
        raise ImageError("그림 파일을 읽을 수 없습니다. PNG·JPG 파일인지 확인해 주세요.") from e

    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    candidates = []
    fmt, size = original
    if fmt in ("png", "jpeg", "webp", "gif") and size == im.size:
        candidates.append((f"image/{fmt}", data))  # 크기·방향을 바꿀 필요가 없으면 원본도 후보
    png = io.BytesIO()
    im.save(png, "PNG", optimize=True)
    candidates.append(("image/png", png.getvalue()))
    if not has_alpha:
        jpg = io.BytesIO()
        im.convert("RGB").save(jpg, "JPEG", quality=85, optimize=True)
        candidates.append(("image/jpeg", jpg.getvalue()))

    mime, out = min(candidates, key=lambda c: len(c[1]))
    return f"data:{mime};base64,{base64.b64encode(out).decode('ascii')}"


class PageSource:
    """첨부 파일(이미지·PDF)의 쪽 그림을 한 번만 만들어 두고, AI가 알려준 영역을 잘라 준다."""

    PDF_SCALE = 2.0  # 약 144dpi: 잘라 낸 그림이 인쇄에 쓸 만한 해상도

    def __init__(self, files: list[tuple[str, bytes]]):  # (mime_type, data)
        self.files = files
        self._cache: dict[tuple[int, int], object] = {}

    def page(self, file_no: int, page_no: int):
        """1부터 세는 첨부 번호·쪽 번호의 PIL 이미지. 없으면 None."""
        from PIL import Image, ImageOps

        key = (file_no, page_no)
        if key in self._cache:
            return self._cache[key]
        img = None
        if 1 <= file_no <= len(self.files):
            mime, data = self.files[file_no - 1]
            try:
                if mime == "application/pdf":
                    import pypdfium2 as pdfium

                    pdf = pdfium.PdfDocument(data)
                    if 1 <= page_no <= len(pdf):
                        img = pdf[page_no - 1].render(scale=self.PDF_SCALE).to_pil()
                elif page_no in (0, 1):
                    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
                    img.load()
            except Exception:  # 손상된 파일 등: 그림 자리만 남긴다
                img = None
        self._cache[key] = img
        return img

    def crop(self, file_no: int, page_no: int, box: list) -> str:
        """box = [ymin, xmin, ymax, xmax] (쪽 전체를 0~1000으로 본 좌표). 실패하면 빈 문자열."""
        img = self.page(file_no, page_no)
        if img is None or not isinstance(box, (list, tuple)) or len(box) != 4:
            return ""
        try:
            y0, x0, y1, x1 = (max(0.0, min(1000.0, float(v))) for v in box)
        except (TypeError, ValueError):
            return ""
        if y1 - y0 < 15 or x1 - x0 < 15:  # 너무 작은 영역(점·아이콘)은 버린다
            return ""
        w, h = img.size
        pad = 8  # 테두리가 잘리지 않게 사방 0.8% 여유
        left, top = int(max(0, x0 - pad) * w / 1000), int(max(0, y0 - pad) * h / 1000)
        right, bottom = int(min(1000, x1 + pad) * w / 1000), int(min(1000, y1 + pad) * h / 1000)
        out = io.BytesIO()
        img.crop((left, top, right, bottom)).convert("RGB").save(out, "PNG")
        return to_data_uri(out.getvalue())


def data_uri_bytes(uri: str) -> bytes:
    return base64.b64decode(uri.split(",", 1)[1]) if "," in uri else b""
