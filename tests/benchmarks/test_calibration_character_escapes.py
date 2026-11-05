"""Character escape recovery applies to prose as well as structured history."""

import base64
import json
from pathlib import Path
from urllib.request import DataHandler, Request

import pytest

from benchmarks.agentic.protocol import KeyLeakError
from benchmarks.grading import calibration_history
from benchmarks.grading.calibration import g1_entries
from benchmarks.grading.calibration_history import CalibrationSource
from benchmarks.grading.calibration_storage import pack_files
from benchmarks.grading.score import _evidence_bytes
from benchmarks.runner import BenchmarkValidationError
from tests.benchmarks.test_calibration_metadata_boundary import MARKER, archive, inject


def escaped(text):
    return r'see \"' + ''.join(r'\u%04x' % ord(c) for c in text) + r'\" done'


def spaced(text, whitespace=' '):
    payload = base64.b64encode(text.encode()).decode()
    return 'data:text/plain;base64,' + whitespace.join(
        payload[i:i + 4] for i in range(0, len(payload), 4)
    )


@pytest.mark.parametrize('slot', ['outer', 'entry', 'tuning', 'binding', 'gate', 'nested'])
@pytest.mark.parametrize('key', [False, True])
def test_stripped_outer_quotes_cannot_hide_retained_metadata(slot, key):
    data = archive()
    inject(data, slot, escaped(MARKER), key=key)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())
    with pytest.raises(KeyLeakError):
        _evidence_bytes(Path('g1.json'), data, calibration=True)
    if slot != 'outer':
        with pytest.raises(KeyLeakError):
            g1_entries(json.dumps(data['entries']).encode(), projection=True)
        with pytest.raises(KeyLeakError):
            _evidence_bytes(Path('g1-entries.json'), data['entries'], calibration=True)


@pytest.mark.parametrize('text', [
    r'the answer uses "\d+" and "C:\Users\history"',
    r'see \"[A-Z]\d+\" done',
    r'old "broken\q" passage',
    r'old "unfinished\u00',
])
@pytest.mark.parametrize('packed', [False, True])
def test_prose_quotes_and_paths_cross_all_boundaries(tmp_path, monkeypatch, text, packed):
    files = {
        'votes/old.json': json.dumps({'text': json.dumps({'explanation': text})}),
        'executions/old/reply.json': json.dumps({'result': {'text': text}}),
        'items/probes/old.json': json.dumps({'user': text}),
    }
    proof = {**(pack_files(files) if packed else {'version': 1, 'files': files}),
             'inputs': {'judge_prompt': text}}
    entries = {'other': {'source': proof, 'notes': text}}
    assert g1_entries(json.dumps({'entries': entries}).encode()) == entries
    assert g1_entries(json.dumps(entries).encode(), projection=True) == entries
    assert json.loads(_evidence_bytes(Path('g1.json'), {'entries': entries},
                                     calibration=True)) == {'entries': entries}
    assert json.loads(_evidence_bytes(Path('g1-entries.json'), entries,
                                     calibration=True)) == entries
    restored, inputs = CalibrationSource.restore(proof)
    assert dict(restored.files) == files
    monkeypatch.setattr(calibration_history, 'PACK_SOURCE_BYTES', 0 if packed else 10**9)
    assert restored.export(inputs) == proof
    produced = CalibrationSource(tmp_path, files).export(inputs)
    assert produced == proof
    # The same harmless paths/regexes must not shelter a supported escaped key.
    files['items/probes/old.json'] = json.dumps({'user': text + ' ' + escaped(MARKER)})
    hostile = {**(pack_files(files) if packed else {'version': 1, 'files': files}),
               'inputs': proof['inputs']}
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(hostile)
    with pytest.raises(KeyLeakError):
        CalibrationSource(tmp_path, files).export(inputs)


@pytest.mark.parametrize('text', [
    spaced(MARKER, ws) for ws in (' ', '\t', '\n', '\r', '\v', '\f', '\r\n', '\u00a0')
] + ['data:text/plain,x)%41' + MARKER[1:]] + [
    'data:text/plain,benign' + ws + '%41' + MARKER[1:]
    for ws in (' ', '\t', '\n', '\r', '\v', '\f', '\u00a0')
])
def test_attachment_never_inspects_only_a_payload_prefix(text):
    assert MARKER.encode() in DataHandler().data_open(Request(text)).read()
    data = archive()
    inject(data, 'entry', text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


@pytest.mark.parametrize('packed', [False, True])
@pytest.mark.parametrize('kind', ['escapes', 'spaced-base64', 'punctuation'])
@pytest.mark.parametrize('slot', ['extension', 'source-key', 'input', 'literal', 'reply', 'item'])
def test_hostile_text_reaches_all_source_producers_and_consumers(tmp_path, packed, kind, slot):
    text = {'escapes': escaped(MARKER), 'spaced-base64': spaced(MARKER),
            'punctuation': 'data:text/plain,x)%41' + MARKER[1:]}[kind]
    files = {}
    inputs = {}
    if slot in ('reply', 'item'):
        name = 'votes/old.json' if slot == 'reply' else 'items/probes/old.json'
        files[name] = json.dumps({'text' if slot == 'reply' else 'user': text})
    elif slot in ('input', 'literal'):
        inputs['panel' if slot == 'input' else 'judge_prompt'] = (
            json.dumps({'notes': text}) if slot == 'input' else text
        )
    proof = {**(pack_files(files) if packed else {'version': 1, 'files': files}),
             'inputs': inputs}
    if slot == 'extension':
        proof['support_note'] = text
    elif slot == 'source-key':
        proof[text] = 'ordinary note'
    entries = {'other': {'source': proof}}
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps({'entries': entries}).encode())
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(entries).encode(), projection=True)
    for name, content in (('g1.json', {'entries': entries}), ('g1-entries.json', entries)):
        with pytest.raises(KeyLeakError):
            _evidence_bytes(Path(name), content, calibration=True)
    with pytest.raises(KeyLeakError):
        CalibrationSource.restore(proof)
    if slot not in ('extension', 'source-key'):
        with pytest.raises(KeyLeakError):
            CalibrationSource(tmp_path, files).export({k: v.encode() for k, v in inputs.items()})


@pytest.mark.parametrize('separator', [')', ']', '}', '`', '<', '>', '\\', '"', "'"])
def test_punctuation_never_truncates_a_percent_attachment(separator):
    data = archive()
    inject(data, 'entry', 'data:text/plain,x' + separator + '%41' + MARKER[1:])
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


@pytest.mark.parametrize('separator', [' ', '\t', '\n', '\r', '\f', ')', ']', '>', '\\'])
def test_noncanonical_attachment_header_is_refused(separator):
    data = archive()
    inject(data, 'entry', 'data:text/plain;' + separator + 'base64,'
           + base64.b64encode(MARKER.encode()).decode())
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


@pytest.mark.parametrize('text', [
    '(data:text/plain;base64,aGVsbG8=) ordinary notes',
    '(data:text/plain;base64,aGVsbG8=). Ordinary notes.',
    '[data:text/plain;base64,aGVsbG8=], ordinary notes',
    '"data:text/plain;base64,aGVsbG8=" ordinary notes',
    'data:text/plain;base64,aGVsbG8= ordinary notes',
    'data:text/plain,x)hello',
    json.dumps({'one': 'data:text/plain;base64,aGVsbG8=', 'two': 'data:,safe'}),
])
def test_canonical_attachment_notes_keep_original_bytes(text):
    data = archive()
    inject(data, 'entry', text)
    assert g1_entries(json.dumps(data).encode()) == data['entries']


def test_multiple_adjacent_quoted_attachments_are_all_inspected():
    text = json.dumps({'one': 'data:text/plain;base64,aGVsbG8=', 'two': spaced(MARKER)})
    data = archive()
    inject(data, 'entry', text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


@pytest.mark.parametrize('text', [
    'data:text/plain,hello %41' + MARKER[1:],
    'data:text/plain,hello ordinary%20%41' + MARKER[1:],
    'data:text/plain ;base64,' + base64.b64encode(MARKER.encode()).decode(),
    'data:\ntext/plain;base64,' + base64.b64encode(MARKER.encode()).decode(),
    'data:te\txt/plain;base64,' + base64.b64encode(MARKER.encode()).decode(),
])
def test_noncanonical_uri_interpretations_cannot_hide_continued_bytes(text):
    data = archive()
    inject(data, 'entry', text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())


@pytest.mark.parametrize('slot', ['vote', 'item', 'yaml'])
def test_actual_source_loaders_still_refuse_invalid_syntax(tmp_path, slot):
    files = {}
    inputs = {}
    if slot == 'yaml':
        inputs['panel'] = r'notes: "broken\q"'
    else:
        files['votes/old.json' if slot == 'vote' else 'items/probes/old.json'] = (
            r'{"notes":"broken\q"}'
        )
    proof = {'version': 1, 'files': files, 'inputs': inputs}
    with pytest.raises(BenchmarkValidationError):
        g1_entries(json.dumps({'entries': {'other': {'source': proof}}}).encode())
    with pytest.raises(BenchmarkValidationError):
        CalibrationSource(tmp_path, files).export({k: v.encode() for k, v in inputs.items()})


@pytest.mark.parametrize('token', ['~', '*', '%', "'", '`', '|'])
def test_unsupported_mime_tokens_are_refused_instead_of_skipped(token):
    text = ('data:application/x' + token + 'test;base64,'
            + base64.b64encode(MARKER.encode()).decode())
    data = archive()
    inject(data, 'entry', text)
    with pytest.raises(KeyLeakError):
        g1_entries(json.dumps(data).encode())
