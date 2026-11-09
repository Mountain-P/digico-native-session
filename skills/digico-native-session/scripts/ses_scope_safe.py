"""Evaluate the Q5 controller Safe predicate using saved state only."""
from __future__ import annotations

from collections import Counter
import struct

from ses_console import CurrentConsole
from ses_recall import _SAFE_NAMES
from ses_snapshot import SnapshotFormatError, SnapshotSection


# tChannel.ControllerSnapshotSafe, Q5 V2025 B25948 and V2242 BD9308.
_MODULE_SAFE = {
    1: 0xf1, 2: 0xf2, 3: 0xf3, 4: 0xf4, 5: 0xf5, 6: 0xf2, 7: 0xf4,
    8: 0xf8, 9: 0xf1, 0x0b: 0xf1, 0x0c: 0xf6, 0x0d: 0xf7, 0x0e: 0xf9,
    0x13: 0xfa, 0x16: 0xf6,
}
_MUSTARD_SAFE = {0x1a: (0x1a, 0xf1), 0x1b: (0x1c, 0xf4), 0x1c: (0x1c, 0xf4),
                 0x1d: (0x1d, 0xf5), 0x1e: (0x1d, 0xf5)}
_MUSTARD_KEYS = frozenset(_MUSTARD_SAFE.values())
_LINKED_ROUTE_MODULES = (0x0f, 0x15, 0x1f)
_READ_MODULES = (0x0c, 2, *_LINKED_ROUTE_MODULES, 0x1a, 0x1c, 0x1d)
_KLANG_PARAMETERS = (0x0b, 0x0e, 0x0f, 0x10)
_KLANG_OPTIONS_OFFSET = 0x10f - 0x24
_MAX_EXPANDED_ITEMS = 4_000_000


def _combine(checks):
    enabled = [reason for value, reason in checks if value is True]
    if enabled:
        return {'safe': True, 'reasons': enabled}
    unresolved = [f'unresolved:{reason}' for value, reason in checks if value is None]
    if unresolved:
        return {'safe': None, 'reasons': unresolved}
    return {'safe': False, 'reasons': ['no_saved_safe']}


def _index_saved_safes(console):
    counts = Counter(channel.channel_id for channel in console.snapshot.channels)
    rows = {}
    remaining = _MAX_EXPANDED_ITEMS
    for channel in console.snapshot.channels:
        channel_id = channel.channel_id
        if counts[channel_id] != 1:
            continue
        saved = rows[channel_id] = {}
        remaining -= 1 + len(channel.modules)
        for module in console._modules(channel):
            remaining -= len(module.controls) // 8
            if remaining < 0:
                raise SnapshotFormatError('Safe inspection exceeds its expanded record limit')
            kind = struct.unpack_from('<i', module.header.effective(58))[0]
            if kind not in _READ_MODULES:
                continue
            for parameter, record_kind, band, _flags, raw in struct.iter_unpack('<BBBB4s', module.controls):
                if record_kind != kind:
                    continue
                if (kind == 0x0c and parameter in _SAFE_NAMES and band == 0
                        or kind == 2 and parameter == 0xf2
                        or (kind, parameter) in _MUSTARD_KEYS and band == 0
                        or kind in _LINKED_ROUTE_MODULES and parameter == 0):
                    key = (kind, parameter, band)
                    # A partial D3 or an ambiguous saved record cannot establish OFF.
                    saved[key] = raw if key not in saved else None
        if remaining < 0:
            raise SnapshotFormatError('Safe inspection exceeds its expanded record limit')
    return rows


class SavedSafeState:
    """Conservative static analogue of Q5 ControllerSnapshotSafe.

    A result of False means all relevant saved Safe inputs are explicitly off.
    A missing, duplicate or undecodable value is unknown unless another check
    already establishes protection. The native decision to call this predicate,
    scope/IgnoreScope and hardware execution belong to the caller.
    """

    def __init__(self, console, snapshots=None):
        if not isinstance(console, CurrentConsole) or console.version not in (2025, 2242):
            raise SnapshotFormatError('Safe inspection requires a Q5 V2025 or V2242 saved console')
        if console.profile is not None and console.profile['id'] != 'quantum5-v2242':
            raise SnapshotFormatError('Safe inspection requires a Q5 saved console')
        self._klang_safe = None
        if snapshots is not None:
            if not isinstance(snapshots, SnapshotSection) or snapshots.version != console.version:
                raise SnapshotFormatError('Safe inspection requires matching Snapshot and console versions')
            if console.module_pool is None and snapshots.modules:
                console = CurrentConsole(console.snapshot, console.version, module_pool=snapshots.modules)
            if len(snapshots.options.data) > _KLANG_OPTIONS_OFFSET:
                raw = snapshots.options.data[_KLANG_OPTIONS_OFFSET]
                if raw in (0, 1):
                    self._klang_safe = bool(raw)
        self._channels = _index_saved_safes(console)

    def _boolean(self, channel_id, module, parameter, band=0):
        raw = self._channels.get(channel_id, {}).get((module, parameter, band))
        if raw is None:
            return None
        value = struct.unpack('<f', raw)[0]
        return bool(value) if value in (0, 1) else None

    def _linked_source(self, channel_id, module, band):
        raw = self._channels[channel_id].get((module, 0, band))
        if raw is None:
            return _combine([(None, 'full_safe_linked_route')])
        route = struct.unpack('<I', raw)[0]
        if not route:
            return {'safe': False, 'reasons': ['linked_route_disconnected']}
        source = self._evaluate(route >> 16, 0xfe, (route & 255) or 0x0e, (route >> 8) & 255)
        return {'safe': source['safe'], 'reasons': [f'linked_source:{reason}' for reason in source['reasons']]}

    def _evaluate(self, channel_id, parameter, module, band):
        if channel_id not in self._channels:
            return _combine([(None, 'saved_channel')])
        full = self._boolean(channel_id, 0x0c, 0xf0)
        if module in _MUSTARD_SAFE:
            # A native Mustard module Safe, when present, replaces Full Safe.
            owner, safe_parameter = _MUSTARD_SAFE[module]
            own = self._boolean(channel_id, owner, safe_parameter)
            return _combine([(own, f'mustard_{_SAFE_NAMES[safe_parameter]}')])
        if parameter == 0 and module in _LINKED_ROUTE_MODULES:
            # Native Full Safe on these route selectors follows the source
            # controller's Safe. D3 missing the route cannot establish its state.
            if full is None:
                return _combine([(None, 'full_safe')])
            if full:
                return self._linked_source(channel_id, module, band)
        checks = [(full, 'full_safe')]
        category = _MODULE_SAFE.get(module)
        if category is not None:
            checks.append((self._boolean(channel_id, 0x0c, category), _SAFE_NAMES[category]))
        if (module, parameter) == (1, 0x0e):
            # C01BD0 loads this setting from outside the .ses file.
            checks.append((None, 'dont_mirror_alt_input'))
        if module == 2:
            checks.append((self._boolean(channel_id, 2, 0xf2, band), 'aux_node_safe'))
            checks.append((self._boolean(0x0500 | band, 0x0c, 0xf2), 'destination_aux_safe'))
            if parameter in _KLANG_PARAMETERS:
                checks.append((self._klang_safe, 'klang_positions_safe'))
        return _combine(checks)

    def evaluate(self, channel_id, parameter, module, band):
        """Return Safe True, False or None and the decisive or unresolved reasons."""
        if (type(channel_id) is not int or not 0 <= channel_id <= 65535
                or any(type(value) is not int or not 0 <= value <= 255 for value in (parameter, module, band))):
            raise SnapshotFormatError('Safe inspection needs a uint16 channel and three uint8 controller fields')
        return self._evaluate(channel_id, parameter, module, band)
