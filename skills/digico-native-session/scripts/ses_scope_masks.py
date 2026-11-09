"""Saved Q5 recall masks reviewed against the V2025 and V2242 native recall paths."""
from __future__ import annotations

from ses_routing import C9Console
from ses_snapshot import ConsoleSnapshot, SnapshotFormatError, SnapshotSection


_GROUP_MODULE_RECALL = 0x21
_GROUP_PARAMETER_RECALL = 0x29
_CHANNEL_SECTION = 0x54
_SECTION_ROW_BYTES = 72
_SNAPSHOT_GROUP_SECTION_BASE = 0x20
_OUTPUT_SOCKET_MODULE = 0x12
# tConsoleSnapshot.Recall overrides these ControllerScope bits from MiscScope.
_MISC_SCOPE_OVERRIDES = {(12, 4): 0x0100, (16, 255): 0x0100,
                         (12, 5): 0x0200, (15, 5): 0x0200}


def _bit(data, offset, bit):
    byte = offset + bit // 8
    return bool(data[byte] & (1 << (bit % 8))) if byte < len(data) else None


def _module_and_parameter(data, module_offset, parameter_offset, module_id, parameter_id):
    module = _bit(data, module_offset, module_id)
    if module is False or parameter_id == 0:
        return module
    parameter = _bit(data, parameter_offset, parameter_id)
    if parameter is False:
        return False
    return True if module is True and parameter is True else None


def _section_permission(data, base, section_id, module_id, parameter_id):
    offset = base + (section_id - 1) * _SECTION_ROW_BYTES
    return _module_and_parameter(data, offset, offset + 8, module_id, parameter_id)


class ScopeMasks:
    """Inspect separate mask layers for an ordinary recall, before Safe checks.

    A cue group replaces the root D2 section mask, so only the selected D2
    decision is applied. The section feature is gated by native runtime state.
    Leave it unspecified to report an unknown if its selected mask would deny.
    """

    def __init__(self, snapshots, topology, *, section_scopes_enabled=None):
        if not isinstance(snapshots, SnapshotSection) or snapshots.version not in (2025, 2242):
            raise SnapshotFormatError('Recall masks require a parsed V2025 or V2242 D2 section')
        if topology is not None and (not isinstance(topology, C9Console) or topology.version != snapshots.version):
            raise SnapshotFormatError('Recall masks require matching D2 and C9 versions')
        if section_scopes_enabled is not None and type(section_scopes_enabled) is not bool:
            raise SnapshotFormatError('Section scope state must be Boolean or None')
        self.snapshots = snapshots
        self.section_scopes_enabled = section_scopes_enabled
        self._groups = {group.group_id: group for group in topology.groups} if topology is not None else {}
        self._channels = {channel.channel_id: channel for group in self._groups.values() for channel in group.channels}

    def evaluate(self, cue, channel_id, module_id, parameter_id):
        if not isinstance(cue, ConsoleSnapshot):
            raise SnapshotFormatError('Recall masks require a parsed cue record')
        for value, limit, name in ((channel_id, 0xffff, 'Channel ID'), (module_id, 255, 'Module ID'),
                                   (parameter_id, 255, 'Parameter ID')):
            if type(value) is not int or not 0 <= value <= limit:
                raise SnapshotFormatError(f'{name} is outside its recall mask range')

        reasons = []
        channel = self._channels.get(channel_id)
        # The right leg uses the left bus's group mask but its own Section ID.
        group_id = channel.buss_id >> 8 if channel is not None and channel_id >> 8 == 7 else channel_id >> 8
        group = self._groups.get(group_id) if channel is not None else None
        section_id = (channel.metadata[_CHANNEL_SECTION]
                      if channel is not None and len(channel.metadata) > _CHANNEL_SECTION else None)
        cue_group = cue.timing.effective(81)[6]
        group_scope = global_section = snapshot_group_section = selected_section = cue_raw = cue_scope = None
        saved_group = None
        section_source = None
        if 1 <= module_id <= 31:
            cue_raw = _bit(cue.controller_scope.data, (module_id - 1) * 32, parameter_id)
            if module_id == _OUTPUT_SOCKET_MODULE:
                cue_scope = True
            elif (module_id, parameter_id) in _MISC_SCOPE_OVERRIDES:
                flags = int.from_bytes(cue.flags.effective(2), 'little')
                cue_scope = bool(flags & _MISC_SCOPE_OVERRIDES[(module_id, parameter_id)])
            else:
                cue_scope = cue_raw
            if cue_scope is None:
                reasons.append('missing_cue_scope_bit')

            if group is not None:
                saved_group = _module_and_parameter(group.metadata, _GROUP_MODULE_RECALL,
                                                     _GROUP_PARAMETER_RECALL, module_id, parameter_id)
                # tGroup.Load resets physical parameter sets to all ones and
                # disables the entire internal-input group 0x1b.
                if group_id == 0x1b:
                    group_scope = False
                elif group_id >= 0x1b:
                    group_scope = _bit(group.metadata, _GROUP_MODULE_RECALL, module_id)
                else:
                    group_scope = saved_group

            if section_id is not None and 1 <= section_id <= 4:
                global_section = _section_permission(self.snapshots.section_scope.data, 0, section_id,
                                                     module_id, parameter_id)
                if 1 <= cue_group <= len(self.snapshots.groups):
                    snapshot_group_section = _section_permission(self.snapshots.groups[cue_group - 1].data,
                                                                 _SNAPSHOT_GROUP_SECTION_BASE, section_id,
                                                                 module_id, parameter_id)

            if module_id == _OUTPUT_SOCKET_MODULE and parameter_id == 0:
                # Native direct socket source-route recall bypasses both masks.
                group_scope, selected_section, section_source = True, True, 'output_socket_route_bypass'
            else:
                if group_scope is None:
                    reasons.append('missing_c9_group_scope' if channel is not None else 'missing_c9_channel')
                selected_section, section_source, reason = self._select_section(
                    section_id, cue_group, global_section, snapshot_group_section)
                if reason:
                    reasons.append(reason)
        else:
            reasons.append('unreviewed_module_id')

        return {
            'c9_group': group_scope, 'c9_group_saved': saved_group, 'group_id': group_id,
            'section_id': section_id, 'section_scopes_enabled': self.section_scopes_enabled,
            'snapshot_group_number': cue_group, 'section_source': section_source,
            'd2_global_section': global_section, 'd2_snapshot_group_section': snapshot_group_section,
            'd2_selected_section': selected_section, 'cue_raw': cue_raw, 'cue': cue_scope,
            'opaque_reasons': reasons,
        }

    def _select_section(self, section_id, cue_group, global_section, snapshot_group_section):
        if self.section_scopes_enabled is False:
            return True, 'disabled', None
        if section_id == 0:
            return True, 'unassigned', None
        if section_id is None or not 1 <= section_id <= 4:
            return None, None, 'unreviewed_c9_channel_section'
        source = 'snapshot_group' if cue_group else 'global'
        state = snapshot_group_section if cue_group else global_section
        if state is None:
            return None, source, 'missing_d2_section_scope'
        if state is False and self.section_scopes_enabled is None:
            return None, source, 'section_feature_unverified'
        return state, source, None
