"""생성 작업의 숫자·분류만 세션에 기록한다. 원문·키·예외 문자열은 받지 않는다."""
from __future__ import annotations

import copy
import json
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from uuid import uuid4

_current: ContextVar = ContextVar('generation_diagnostics', default=None)
_stages = {'AI 응답': 'ai', '1단계 · 초안 설계': 'draft',
           '1단계 · 초안 만들기': 'draft', '2단계 · 내용 검토': 'review', '검토 후 수정': 'repair'}
_events = {'start', 'request_start', 'request_end', 'request_settings', 'wait', 'budget_stop', 'finish'}
_outcomes = {'success', 'busy', 'timeout', 'quota', 'model', 'output', 'key', 'other',
             'pending_review', 'failed', 'interrupted', 'running', 'success_with_review_warning'}
_numbers = {'remaining_seconds', 'duration_seconds', 'timeout_seconds', 'prompt_chars',
            'attachment_count', 'http_status', 'wait_seconds', 'budget_seconds'}


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


@contextmanager
def capture(app_version: str, previous: dict | None = None):
    """재개는 같은 작업 번호에 덧붙인다. 기록은 현재 세션의 사전에만 둔다."""
    report = copy.deepcopy(previous) if previous else {
        'format_version': 1, 'work_id': uuid4().hex, 'events': [], 'attempt': 0, 'dropped_events': 0}
    report['attempt'] += 1
    report['app_version'] = app_version if re.fullmatch(r'\d+(?:\.\d+)*', app_version) else 'unknown'
    report['status'] = 'running'
    try:
        report['sdk_version'] = version('google-genai')
    except PackageNotFoundError:
        report['sdk_version'] = 'unknown'
    token = _current.set((report, time.perf_counter()))
    emit('start')
    try:
        yield report
    finally:
        if report['status'] == 'running':
            finish('interrupted')
        _current.reset(token)


def emit(event: str, *, stage: str | None = None, model: str | None = None,
         outcome: str | None = None, thinking: str | None = None, **values):
    active = _current.get()
    if active is None or event not in _events:
        return
    report, started = active
    row = {'event': event, 'utc': _utc(), 'attempt': report['attempt'],
           'elapsed_seconds': round(max(0, time.perf_counter() - started), 3)}
    if stage is not None:
        row['stage'] = _stages.get(stage, 'ai')
    if model is not None and re.fullmatch(r'gemini-\d+(?:\.\d+)*-flash(?:-lite)?', model):
        row['model'] = model
    if outcome in _outcomes:
        row['outcome'] = outcome
    for key, value in values.items():
        if key in _numbers and type(value) in (int, float) and abs(value) < 1e12:
            row[key] = round(value, 3)
    if thinking in {'minimal', 'low', 'medium', 'high'}:
        row['thinking'] = thinking
    report['events'].append(row)
    if len(report['events']) > 1000:
        report['events'].pop(0)
        report['dropped_events'] += 1


def finish(outcome: str):
    active = _current.get()
    if active is not None:
        active[0]['status'] = outcome if outcome in _outcomes else 'other'
        emit('finish', outcome=active[0]['status'])


def dumps(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
