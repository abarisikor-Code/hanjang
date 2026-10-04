"""한장: 일관된 A4 HTML 학습지 생성기. AI는 구조 분석, 코드는 조판."""

from pathlib import Path


def source_stamp() -> float:
    """이 패키지 파일들 가운데 가장 늦게 바뀐 시각. 불러온 모듈이 지금 파일과 같은 판인지 견주는 데 쓴다 (app._fresh_worksheet_modules)."""
    return max(f.stat().st_mtime for f in Path(__file__).resolve().parent.rglob("*.py"))


_hanjang_stamp = source_stamp()  # 불러온 때의 판
