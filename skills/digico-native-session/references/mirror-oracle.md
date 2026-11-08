# Read-only native Mirror baseline oracle

`scripts/native_mirror_oracle.ps1` computes the recovered native baseline from a caller-provided read-only memory reader. It does not select/open a process, call target functions, write target memory, open sockets, build protocol bodies/frames, send acceptance, load/save a Session or negotiate mirroring. It is a library building block, with no CLI/MCP mode added.

## API and identity

```powershell
. ./scripts/native_mirror_oracle.ps1
$baseline = Read-MirrorVerifyBaseline -Reader $reader `
    -ImageBase $actualImageBase -BuildProfile $hashGatedProfile
```

The caller must independently bind the selected executable/process, verify its actual executable SHA256 and obtain its actual32-bit module base. `$reader` must be a .NET object exposing `byte[] Read(Int64 address, Int32 length)` with read-only access. The helper checks the supplied profile object's `id` and `executable.sha256` against its exact supported constants, but does not itself hash files or prove the caller's process selection. Passing a profile ID string alone is rejected.

| Profile | Exact executable SHA256 | Evidence |
|---|---|---|
| `quantum1-v2126` | `535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d` | Recovered four-term algorithms; one bounded isolated Windows offline compatibility exchange observed acceptance and subsequent state traffic |
| `quantum1-v2242-4` | `ca5b3266615b89dbbfc38583c7df837faf734180865de1736720b10ee138ee08` | Recovered five-term algorithms and synthetic readers; native compatibility exchange remains static-only |

The older build's observation does not establish the newer build's acceptance, a complete initial mirror feed, physical-console operation, or audio behavior. The result separately reports the build's evidence stage and always leaves `current_call_peer_acceptance_observed`, `runtime_acceptance_verified` and `initial_sync_complete` false: a read-only oracle cannot observe a peer reply during this call.

## Returned facts

- `hash_terms`, `hash_sum_uint32` and `hash_sum_le_hex`: actual native uint32 wrapping additions, including recursive Snapshot/Macro command data and optional Encoder/ChannelMacro lists; the newer Q1 adds Setlist state. Zero Snapshot count does not imply zero hash.
- `console_record_raw_hex`: the copied123-byte native record, with no invented structure-setter semantics.
- `session_path_raw_hex`, `session_path_ascii`, `normalized_session_path_hex`: observed Pascal bytes, decoded ASCII reference and normalized raw reference bytes. The current reference must contain no embedded NUL or non-ASCII bytes. The normalized field has no newly constructed Pascal prefix.
- `second_string_raw_hex`: a copied native Pascal string whose business meaning remains unassigned; no text guess is made.
- `mode_flag_a_raw`, `mode_flag_b_raw`, canonical0/1 values and `native_local_mode_gate_passed`: the recovered one-mode predicate, not a full compatibility verdict or authorization to send.
- `rtl_codepage`, `signature_source_codepoints_hex`, `signature_wire_hex` and `signature_wire_byte_length`: source codepoints and the separate actual converted wire-byte value. The signature is not assumed to be100 bytes.
- `witnesses`, copied-byte/range/object counts and `consistent_witness_reread`: all source ranges and lengths, copied raw bytes and their final reread consistency check. Results can contain private Session metadata and should stay local.

There is deliberately no `verify_body_hex_dir0`, packet, frame or acceptance-reply output. The helper never assembles the complete protocol body. `packet_or_body_constructed=false` and `network_used=false` describe that boundary.

## Algorithm and stability limits

The hash uses raw DWORD bit patterns and native byte extents, rather than converting every value to a float or using count/name fingerprints. Native boundary quirks are preserved, including the command byte-length word's checksum extent, different name-prefix treatment and the MIDI checksum's data-minus-one starting address. These are additive compatibility checks, not cryptographic signatures.

The port signature reads50 native record pairs. It appends their values as Unicode codepoints, applies the recovered special-type bit mask, and reproduces the native iterative ShortString/Unicode roundtrip using client `kernel32.MultiByteToWideChar` / `WideCharToMultiByte`, flags0 and the observed target RTL codepage. Explicit code-unit/byte lengths preserve embedded NULs. A synthetic UTF8 fixture produces102 bytes from100 source codepoints. The raw source codepoints must not substitute for the converted `signature_wire_hex` when constructing an independent passive `VerifyBaseline` elsewhere.

The helper validates exact VMTs, native alias targets, pointer extents, list counts/capacities and bounded strings. Every copied range is reread after computation; mismatch, unsupported identity/codepage, invalid bounds, missing memory or exhausted budget throws. Defaults are64MiB copied-byte budget and100000 list items; optional `-MaxCopiedBytes` and `-MaxListItems` stay within their declared resource bounds. These are local research limits, not console capacity specifications.

Rereads do not freeze the process or create an atomic snapshot; `values_atomic=false`. This module does not infer that every current mixer value is covered by the compatibility hash, nor that a later peer would accept it. The [Mirror research reference](mirroring-research.md) describes the separately recovered comparison and direction semantics without providing a live peer API.

## Synthetic test

The Windows-only fixture reader uses invented memory and example names/reference bytes; it does not open a target process, socket or Session file. It covers independent hand-computed wrapping sums, recursive structures, old/new term differences, codepage/NUL conversion, mode flags, alias/VMT/count/hash rejection, changed source ranges, and the absence of body/network outputs.

```powershell
& ./tests/native_mirror_oracle_tests.ps1 `
    -OracleModulePath ./skills/digico-native-session/scripts/native_mirror_oracle.ps1
```
