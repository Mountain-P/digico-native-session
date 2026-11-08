"""Strict, local build selection shared by native planners and RPM helpers.

A product name or protocol version is never a compatibility test. Profiles are
bundled reviewed data; executable SHA-256 and the profile ID must agree. An
unrecognized file stays unsupported even when its product is Quantum.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
import inspect
import json
from pathlib import Path
import re
from types import MappingProxyType

DEFAULT_PROFILE_ID = 'quantum1-v2126'
PROFILE_DIRECTORY = Path(__file__).resolve().parent.parent / 'assets' / 'build-profiles'
_IMPLEMENTATIONS = frozenset(('quantum1-v2126-layout-v1', 'quantum-layout-v2'))
_ACTIVE_PROFILE = ContextVar('digico_native_build_profile', default=None)
_DOCUMENT_KEYS = ('inventory', 'catalog', 'presets', 'presets_after', 'before',
                  'after', 'control_contract', 'native_route_contract',
                  'route_contract', 'group_contract',
                  'response_pointer_binding', 'control_write_authorization',
                  'group_assignment_contract', 'preset_inventory_binding')
_CONTRACT_LIST_KEYS = ('steps', 'processing_checks')


class BuildProfileError(ValueError):
    """An artifact, plan or executable has no matching verified profile."""


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def validate_layouts(profile):
    """Bound the record windows used by collectors before any process reads."""
    layouts = profile.get('layouts', {})
    required = {
        'controller': {'id':5, 'definition':4, 'parent':4, 'value':4,
                       'target':4, 'output':4, 'audio_id':8, 'restore':4},
        'console_list': {'parent':4},
        'module': {'id':5, 'definition':4, 'n_signals':4},
        'channel': {'id':2, 'logical':2, 'name':32, 'stereo_mode':1,
                    'input_route':4, 'alt_input_route':4, 'key_route':4,
                    'buss_routes':32, 'output_sockets':4},
        'definition': {'data_type':1, 'default':4, 'band':1, 'parameter':1,
                       'name':32, 'units':3, 'seed':4, 'lut':4},
    }
    for name, fields in required.items():
        layout = layouts.get(name, {})
        size = layout.get('size')
        if type(size) is not int or not 4 <= size <= 1048576:
            raise BuildProfileError('Missing or invalid layout size: '+name)
        for field, width in fields.items():
            offset = layout.get(field)
            if type(offset) is not int or offset < 0 or offset+width > size:
                raise BuildProfileError('Missing or invalid layout field: '+name+'.'+field)
    flags = layouts['definition'].get('data_flags')
    if flags is not None and (type(flags) is not int or not 0 <= flags < layouts['definition']['size']):
        raise BuildProfileError('Invalid definition.data_flags field')
    colour = layouts['definition'].get('colour')
    if colour is not None and (type(colour) is not int or not 0 <= colour <= layouts['definition']['size']-4):
        raise BuildProfileError('Invalid definition.colour field')
    for name in ('current_response_seed','current_response_lut'):
        offset = layouts['controller'].get(name)
        if offset is not None and (type(offset) is not int or not 0 <= offset <= layouts['controller']['size']-4):
            raise BuildProfileError('Invalid controller.'+name+' field')
    return True


def _validate_profile(document, source, *, runnable=True):
    if (not isinstance(document, dict) or type(document.get('schema_version')) is not int
            or document.get('schema_version') != 1
            or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', str(document.get('id', '')))):
        raise BuildProfileError('Invalid build profile: '+str(source))
    executable = document.get('executable', {})
    if (not re.fullmatch('[0-9a-f]{64}', str(executable.get('sha256', '')))
            or type(executable.get('pointer_size')) is not int or executable.get('pointer_size') != 4
            or type(executable.get('image_size')) is not int
            or not 0 < executable['image_size'] <= 0xffffffff):
        raise BuildProfileError('Invalid executable identity in '+str(source))
    if runnable and document.get('implementation') not in _IMPLEMENTATIONS:
        raise BuildProfileError('No reviewed helper implementation for '+document['id'])
    if runnable and document.get('validation', {}).get('offline_runtime_verified') is not True:
        raise BuildProfileError('Build profile has no verified offline runtime: '+document['id'])
    if runnable:
        validate_layouts(document)
        validate_control_write_allowlist(document)
    return _freeze(document)


def validate_control_write_allowlist(profile):
    rules = profile.get('control_write_allowlist')
    if rules is None:
        return True  # v2 treats absent/null as deny-all, never as legacy access.
    if not isinstance(rules, list) or len(rules) > 4096:
        raise BuildProfileError('control_write_allowlist must be a bounded array or null')
    fields = {'id_prefix_hex', 'raw_type', 'definition_rva', 'module_vmt_rva', 'process_output_rva'}
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or not fields <= rule.keys() or rule.keys()-fields-{'evidence'}:
            raise BuildProfileError('Invalid native control allowlist fields')
        if not isinstance(rule['id_prefix_hex'], str) or not re.fullmatch('[0-9a-f]{6}', rule['id_prefix_hex']):
            raise BuildProfileError('Control allowlist prefix must be exactly three lowercase raw ID bytes')
        if type(rule['raw_type']) is not int or not 0 <= rule['raw_type'] <= 4:
            raise BuildProfileError('Invalid control allowlist datatype')
        for name in ('definition_rva', 'module_vmt_rva', 'process_output_rva'):
            if type(rule[name]) is not int or not 0 < rule[name] < profile['executable']['image_size']:
                raise BuildProfileError('Invalid control allowlist '+name)
        key = tuple(rule[name] for name in sorted(fields))
        if key in seen:
            raise BuildProfileError('Duplicate native control allowlist rule')
        seen.add(key)
    return True


def load_profile_record(path):
    """Read static/candidate metadata without registering or admitting a build.

    This result is deliberately not accepted by select_profile/use_profile.
    Private laboratory probes can inspect its exact hash and evidence stages.
    """
    source = Path(path)
    return _validate_profile(json.loads(source.read_text(encoding='utf-8')),
                             source.name, runnable=False)


@lru_cache(maxsize=1)
def _registry():
    by_id = {}; by_hash = {}
    for path in sorted(PROFILE_DIRECTORY.glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        _validate_profile(record, path.name, runnable=False)
        if record.get('validation', {}).get('offline_runtime_verified') is not True:
            continue
        profile = _validate_profile(record, path.name)
        digest = profile['executable']['sha256']
        if profile['id'] in by_id or digest in by_hash:
            raise BuildProfileError('Ambiguous build profile ID or executable SHA-256')
        by_id[profile['id']] = profile; by_hash[digest] = profile
    if not by_id:
        raise BuildProfileError('No bundled verified build profiles are available')
    return by_id, by_hash


def select_profile(executable_sha256=None, build_profile=None):
    """Select only a bundled verified profile; never infer from a filename."""
    by_id, by_hash = _registry()
    if build_profile is not None and (not isinstance(build_profile, str) or build_profile not in by_id):
        raise BuildProfileError('Unknown or unverified build_profile: '+str(build_profile))
    if executable_sha256 is not None:
        if not isinstance(executable_sha256, str) or executable_sha256 not in by_hash:
            raise BuildProfileError('Unknown or unverified executable SHA-256')
        profile = by_hash[executable_sha256]
        if build_profile is not None and profile['id'] != build_profile:
            raise BuildProfileError('build_profile and executable SHA-256 identify different builds')
        return profile
    if build_profile is not None:
        return by_id[build_profile]
    return _ACTIVE_PROFILE.get() or by_id[DEFAULT_PROFILE_ID]


def profile_identity(profile=None):
    profile = select_profile() if profile is None else profile
    return {'build_profile': profile['id'], 'executable_sha256': profile['executable']['sha256']}


def profile_for_document(document, *, expected=None, feature=None, require_hash=True):
    """Check every envelope identity, including nested inventory/contracts.

    Legacy results may omit build_profile but must supply the exact SHA-256.
    New results and every newly compiled contract carry both fields.
    """
    if not isinstance(document, dict):
        raise BuildProfileError('Build-bound document must be an object')
    identities = []; hashes = []
    def visit(value):
        if not isinstance(value, dict):
            return
        identity = value.get('build_profile')
        legacy = value.get('profile')
        if identity is None and isinstance(legacy, str) and legacy.startswith('quantum'):
            identity = legacy
        digest = value.get('executable_sha256')
        if digest is not None or identity is not None:
            identities.append(select_profile(digest, identity))
        if digest is not None:
            hashes.append(digest)
        for key in _DOCUMENT_KEYS:
            visit(value.get(key))
        for key in _CONTRACT_LIST_KEYS:
            for item in value.get(key, ()) if isinstance(value.get(key), (list, tuple)) else ():
                visit(item)
    visit(document)
    if require_hash and not hashes:
        raise BuildProfileError('Build-bound document is missing executable_sha256')
    profile = expected or (identities[0] if identities else select_profile())
    for copied in identities:
        if copied['id'] != profile['id'] or copied['executable']['sha256'] != profile['executable']['sha256']:
            raise BuildProfileError('Mixed build identities in native document')
    if feature is not None and profile.get('features', {}).get(feature) is not True:
        raise BuildProfileError('Build profile does not support '+feature)
    return profile


@contextmanager
def use_profile(executable_sha256=None, build_profile=None):
    profile = select_profile(executable_sha256, build_profile)
    token = _ACTIVE_PROFILE.set(profile)
    try:
        yield profile
    finally:
        _ACTIVE_PROFILE.reset(token)


def build_value(path):
    """Read data from the selected build without mutating shared module state."""
    value = select_profile()
    for part in path.split('.'):
        try:
            value = value[part]
        except (KeyError, TypeError) as error:
            raise BuildProfileError('Selected build has no verified value: '+path) from error
    return value


def stamp_profile(document, profile=None):
    """Stamp plans and nested native contracts, without walking controller rows."""
    if not isinstance(document, dict):
        return document
    identity = profile_identity(profile)
    document.update(identity)
    for key in _DOCUMENT_KEYS:
        if key in ('before', 'after', 'inventory', 'catalog', 'presets', 'presets_after'):
            continue
        if isinstance(document.get(key), dict):
            stamp_profile(document[key], profile)
    for key in _CONTRACT_LIST_KEYS:
        for item in document.get(key, ()) if isinstance(document.get(key), (list, tuple)) else ():
            stamp_profile(item, profile)
    return document


def profile_bound(*, feature, documents=(), error_type=BuildProfileError):
    """Bind a pure public helper to one profile; accept optional build_profile kw.

    ContextVar keeps concurrent requests isolated. The decorator checks all
    declared input artifacts before any parsing or payload generation.
    """
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def invoke(*args, **kwargs):
            requested = kwargs.pop('build_profile', None)
            bound = signature.bind(*args, **kwargs)
            try:
                profile = select_profile(build_profile=requested) if requested is not None or _ACTIVE_PROFILE.get() else None
                for name in sorted(documents, key=lambda name: name in ('request', 'step')):
                    value = bound.arguments.get(name)
                    if isinstance(value, dict):
                        profile = profile_for_document(value, expected=profile, feature=feature,
                                                       require_hash=name != 'request' and name != 'step')
                profile = profile or select_profile()
                if profile.get('features', {}).get(feature) is not True:
                    raise BuildProfileError('Selected build does not support '+feature)
            except BuildProfileError as error:
                raise error_type(str(error)) from error
            token = _ACTIVE_PROFILE.set(profile)
            try:
                result = function(*args, **kwargs)
                return stamp_profile(dict(result) if isinstance(result, dict) else result, profile)
            finally:
                _ACTIVE_PROFILE.reset(token)
        return invoke
    return decorate
