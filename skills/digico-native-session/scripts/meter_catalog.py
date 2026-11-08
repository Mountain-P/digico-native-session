"""Decode copied native meter records; no process access, I/O or network.

The RPM helper exports tDMAList controller output+2C and secondary+50, not the
ordinary scalar+18. These are cached native codes, with no audio/freshness proof.
"""
from __future__ import annotations

from build_profiles import build_value, profile_bound
import math
import re
import struct

TARGET_SHA256 = build_value('executable.sha256')
MANAGER_VMT_RVA = build_value('addresses.hex_7544a4')


class MeterError(ValueError):
    pass


def _hex(value, size, label):
    if not isinstance(value, str) or re.fullmatch('[0-9a-fA-F]{%d}' % (2*size),value) is None:
        raise MeterError('%s requires exactly %d hexadecimal bytes' % (label,size))
    return bytes.fromhex(value)


def _int(value,label,low,high):
    if type(value) is not int or not low <= value <= high:
        raise MeterError('%s must be an integer in [%d,%d]' % (label,low,high))
    return value


def scalar(raw_hex):
    raw=_hex(raw_hex,4,'meter scalar');value=struct.unpack('<f',raw)[0]
    finite=math.isfinite(value)
    code=int(value) if finite and 0<=value<=65535 and value.is_integer() else None
    return {'raw_hex':raw.hex(),'float32':value if finite else None,'finite':finite,'native_code':code}


def category9_words(raw_hex):
    """Decode the two original unsigned16 words; never apply float32 decoding."""
    first,second=struct.unpack('<HH',_hex(raw_hex,4,'Category9 meter words'))
    return {'word0':first,'word1':second,'signed_interpretation':False}


def streamer_word(raw_hex):
    """B57634: uint16 stream index and two unsigned8 level codes, little endian."""
    raw=_hex(raw_hex,4,'streamer word')
    return {'stream_index':int.from_bytes(raw[2:],'little'),
            'level_code':raw[0],'secondary_code':raw[1]}


def standard0_display(code,meter_kind):
    """Native display helper 75C964, standard0, ordinary level kinds only.

    75C834/75C964 use floor135, min-60,max0,offset0 => (code-255)/2.
    This is the editor's numeric display scale, not a calibrated audio measure.
    0 is suppressed/below floor;1 is a native special marker in value formatting.
    Gain reduction, gate/share and other meter kinds have no universal mapping.
    """
    code=_int(code,'native code',0,65535)
    if meter_kind not in (0,1,2) or type(meter_kind) is not int:
        return None
    if not 2<=code<=255:
        return None
    return {'value':(code-255)/2.0,'unit':'native-display-dB',
            'source_function_va':('0x%X' % build_value('addresses.hex_75c964')),'standard':0,
            'physical_dbfs_verified':False}


def _copied_scalar(value,label):
    if not isinstance(value,dict):raise MeterError(label+' must be a copied scalar object')
    decoded=scalar(value.get('raw_hex'))
    if 'finite' in value and (type(value['finite']) is not bool or value['finite']!=decoded['finite']):
        raise MeterError(label+' finite flag disagrees with raw bytes')
    if 'native_code' in value and value['native_code']!=decoded['native_code']:
        raise MeterError(label+' integer code disagrees with raw bytes')
    # Human-readable JSON float fields are summaries; the exact4 bytes own value.
    return decoded


@profile_bound(feature='meters', documents=('snapshot',), error_type=MeterError)
def normalize_meter_snapshot(snapshot):
    """Validate the copied Read-NativeMeters result and derive bounded displays."""
    if not isinstance(snapshot,dict) or type(snapshot.get('schema_version')) is not int or snapshot['schema_version']!=1:
        raise MeterError('meter snapshot requires schema_version1')
    if snapshot.get('executable_sha256')!=build_value('executable.sha256'):
        raise MeterError('meter executable does not match the exact V2126 profile')
    for key,wanted in (('memory_read_only',True),('structure_verified',True),
                       ('network_used',False),('ui_used',False),('physical_audio_verified',False)):
        if snapshot.get(key) is not wanted:
            raise MeterError('meter snapshot must report '+key+'='+str(wanted))
    manager=snapshot.get('manager')
    if not isinstance(manager,dict) or manager.get('vmt_rva')!=build_value('addresses.hex_7544a4'):
        raise MeterError('unknown native meter manager VMT')
    total=_int(manager.get('registered_count'),'registered count',0,200000)
    streams=_int(manager.get('stream_count'),'stream count',0,8192)
    rows=snapshot.get('meters')
    if not isinstance(rows,list) or len(rows)>1000:raise MeterError('meters must be a bounded array')
    if type(snapshot.get('returned_count')) is not int or snapshot['returned_count']!=len(rows):
        raise MeterError('returned meter count disagrees with rows')
    matching=_int(snapshot.get('matching_count'),'matching count',0,total)
    if matching<len(rows) or snapshot.get('truncated') is not (matching>len(rows)):
        raise MeterError('meter matching/truncated counters disagree')
    seen=set();by_audio={};by_stream={};meters=[]
    for row in rows:
        if not isinstance(row,dict):raise MeterError('meter row must be an object')
        index=_int(row.get('registry_index'),'registry index',0,max(0,total-1))
        if total==0 or index in seen:raise MeterError('duplicate/outside meter registry index')
        seen.add(index)
        control=_hex(row.get('raw_id_hex'),5,'meter control ID')
        if row.get('channel_id')!=int.from_bytes(control[3:],'little') or row.get('raw_type')!=3:
            raise MeterError('meter ID/channel/type disagree')
        stream=row.get('stream_index')
        if stream is not None:stream=_int(stream,'stream index',0,max(0,streams-1))
        audio=_hex(row.get('audio_id_raw_hex'),8,'meter AudioID').hex()
        if stream is not None:
            if streams==0 or audio=='0'*16:raise MeterError('stream meter needs a nonzero AudioID')
            if audio in by_audio and by_audio[audio]!=stream:raise MeterError('AudioID alias has inconsistent stream index')
            if stream in by_stream and by_stream[stream]!=audio:raise MeterError('stream index maps to multiple AudioIDs')
            by_audio[audio]=stream;by_stream[stream]=audio
        primary=_copied_scalar(row.get('output'),'output')
        secondary=_copied_scalar(row.get('secondary'),'secondary')
        ordinary=_copied_scalar(row.get('ordinary_current'),'ordinary current')
        kind_scalar=_copied_scalar(row.get('meter_type'),'meter kind')
        kind=kind_scalar['native_code']
        display=None
        if manager.get('meter_standard')==0 and primary['native_code'] is not None:
            display=standard0_display(primary['native_code'],kind)
        meters.append({'id':control.hex(),'channel_id':row['channel_id'],
            'name':row.get('name'),'registry_index':index,'stream_index':stream,
            'audio_id_raw_hex':audio,'meter_kind':kind,
            'output':primary,'secondary':secondary,'ordinary_current':ordinary,
            'display':display,'unit':'native-meter-code','freshness':'not_observable',
            'physical_audio_verified':False})
    return {'schema_version':1,'executable_sha256':build_value('executable.sha256'),
            'memory_read_only':True,'network_used':False,'ui_used':False,
            'physical_audio_verified':False,'values_atomic':False,
            'freshness':'not_observable','manager':dict(manager),'meters':meters,
            'matching_count':matching,'returned_count':len(meters),
            'truncated':matching>len(meters)}
