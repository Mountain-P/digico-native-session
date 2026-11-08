"""Inspect flushed native receipts without contacting an editor or replaying work."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


class JournalError(ValueError):
    pass


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise JournalError('duplicate journal JSON field: ' + key)
        value[key] = item
    return value


def _json(text):
    return json.loads(text, object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(JournalError('nonfinite journal number')))


def read_journal(path, *, max_bytes=32 * 1024 * 1024):
    path = Path(path)
    raw = path.read_bytes()
    if not raw or len(raw) > max_bytes:
        raise JournalError('journal is empty or exceeds its local resource bound')
    records = []
    previous = '0' * 64
    incomplete = False
    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        # An unterminated tail can have been torn by process death; it has no
        # authority, even if it happens to contain syntactically valid JSON.
        if not line.endswith(b'\n'):
            if index != len(lines) - 1:
                raise JournalError('unterminated non-tail journal record')
            incomplete = True
            break
        try:
            envelope = _json(line.decode('utf-8'))
            if not isinstance(envelope, dict) or set(envelope) != {'payload_json', 'sha256'}:
                raise JournalError('invalid journal record envelope')
            text = envelope['payload_json']
            if not isinstance(text, str) or not isinstance(envelope['sha256'], str):
                raise JournalError('invalid journal record types')
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if digest != envelope['sha256']:
                raise JournalError('journal record digest differs')
            record = _json(text)
        except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise JournalError('invalid complete journal record') from error
        if (not isinstance(record, dict) or type(record.get('schema_version')) is not int
                or record['schema_version'] != 1 or type(record.get('sequence')) is not int
                or record['sequence'] != len(records) or record.get('previous_sha256') != previous
                or not isinstance(record.get('event'), str) or not isinstance(record.get('data'), dict)):
            raise JournalError('journal sequence or chain differs')
        records.append(record)
        previous = digest
    if not records or records[0]['event'] != 'request_started':
        raise JournalError('journal has no complete request identity')
    request_hash = records[0]['data'].get('request_sha256')
    if not isinstance(request_hash, str) or not re.fullmatch('[0-9a-f]{64}', request_hash):
        raise JournalError('journal request hash is invalid')
    if any(record['event'] == 'request_started' for record in records[1:]):
        raise JournalError('journal contains more than one request')
    return {'schema_version': 1, 'request_sha256': request_hash, 'records': records,
            'tail_incomplete': incomplete, 'last_sha256': previous,
            'hash_chain_verified': True, 'authenticated': False}


def recovery_report(journal, *, compiled_request_path=None):
    """Classify observed progress; deliberately return no executable retry plan."""
    if compiled_request_path is not None:
        raw = Path(compiled_request_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != journal['request_sha256']:
            raise JournalError('compiled request does not match this journal')
    started, verified = {}, {}
    finished = None
    for record in journal['records']:
        event, data = record['event'], record['data']
        if finished is not None:
            raise JournalError('records occur after request completion')
        if event in ('step_started', 'step_verified'):
            index = data.get('operation_index')
            if type(index) is not int or index < 0:
                raise JournalError('invalid journal operation index')
            if event == 'step_started':
                if index in started or any(key not in verified for key in started):
                    raise JournalError('overlapping or repeated journal operation')
                started[index] = data
            else:
                if index not in started or index in verified or data.get('receipt', {}).get('verified') is not True:
                    raise JournalError('verified receipt has no matching started operation')
                verified[index] = data
        elif event == 'request_finished':
            finished = data
    unresolved = [index for index in started if index not in verified]
    return {'schema_version': 1, 'request_sha256': journal['request_sha256'],
            'hash_chain_verified': True, 'tail_incomplete': journal['tail_incomplete'],
            'request_outcome': finished.get('status') if finished else 'not_recorded',
            'verified_operation_indices': sorted(verified),
            'indeterminate_operation_indices': unresolved,
            'verified_receipts': [verified[key] for key in sorted(verified)],
            'network_used': False, 'target_called': False, 'automatic_replay_allowed': False,
            'fresh_state_required': True,
            'next_action': 'Inspect a new target state and compare it with the compiled request before planning remaining work.'}
