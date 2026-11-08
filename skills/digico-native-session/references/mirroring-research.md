# Native Mirror initial-sync research

This document separates shipped-code observations, bounded private-lab evidence,
and experiment design.
`mirror_state.py` is a passive decoder/evidence ledger, not a full mirror peer.
The [read-only Mirror oracle](mirror-oracle.md) supplies independently copied compatibility facts; it never builds or sends a Verify packet.

## Evidence and build boundary

Static targets were independently extracted Windows x86 executables:

| Build | SHA-256 |
|---|---|
| Quantum1 V2126 | `535097f38bb459e6e299b5da50b62fa811511d92af6d7215a13d703a416f177d` |
| Quantum1 V2242.4 | `ca5b3266615b89dbbfc38583c7df837faf734180865de1736720b10ee138ee08` |

Existing V2126 REA/Ghidra records include SessionVerify sender
`ev_11cba8d3d2b8c2ff82d73f0db97b7e0b68aa78c1642c9097cb445be739ec4e8b`,
receiver `ev_a0d70351c41a0b9d4426cf06cee96cbb689d6ae4d0f2c09b490d1e2e23324809`,
and reply receiver
`ev_20b4027c8d49f58cbd964b76bf8645bd42c6bc7159b92aa338b01c7a09dc0397`.
New cross-build entry candidates came from PE relocation/call-masked byte-prefix
matching, followed by bounded Capstone inspection of the actual instruction
paths. Candidate matching alone was not treated as semantic equivalence.
Addresses below are preferred-image VAs, image base `0x400000`.

The manufacturer describes session synchronization before mirroring, explicit
Mirror To/From directions, and Remote/One Way/Full Mirror/Expander operating
modes. These are product semantics, not recovered numeric wire-mode labels.
See the [manufacturer Software Reference, Network and Mirroring](https://support.digico.biz/hc/en-gb/article_attachments/37137993915665).

## Bounded V2126 lab observation

An isolated Quantum1 V2126 probe sent exactly one independently verified direction0 SessionVerify and observed the native accepted reply with 372,543 received bytes, 514 scalar control messages, 30 state batches and 30,199 observed control IDs; no unsupported flag records were found, while three other messages remained opaque.
Post-disconnect checks preserved the source-file hash, selected numeric current/target/output bytes and Session path/FPS/console identity, but no end marker was established: `initial_sync_complete=false` and `end_marker_verified=false`, and this private probe does not expose a CLI/MCP or live-Mirror feature.

## Observed normal connection sequence

1. Enable Console Network on both original endpoints; a changed setting requires
   the manufacturer's normal restart. Actual listener scope must be checked:
   the INI option does not establish loopback-only binding.
2. UDP discovery uses port2029 and normal binary framing. The original worker
   advertises every2000ms. Connection election compares raw `inet_addr` DWORDs;
   one endpoint opens TCP2028 and the other accepts. TCP2029 is a separate
   status-only listener, not the mirror stream.
3. Category0/sub1 identity negotiates the software-version word and compatibility
   name prefix. The native states are0 disconnect,1 request,2 positive reply.
   `Connected` becomes true only on the accepting identity branch. A TCP socket
   alone does not establish this application gate.
4. Synchronize the intended Session. Native file transfer is Category4 with
   block/ACK semantics. An empty source filename can invoke native Save first;
   receiving Session/file commands can write files or Load. File transfer is
   not automatically read-only.
5. Mirror To/From invokes SessionVerify, Category4/sub6. The peer evaluates the
   recovered compatibility conditions and returns sub7 with accepted and
   direction bytes. It also changes native mirror flags and notifies peers.
6. On successful compatibility, the selected source emits current surface
   values. Normal mirror deltas then use Category8 or Category9.

No new raw-host or send API is provided by this research module.

The [read-only baseline oracle](mirror-oracle.md) supplies recovered hashes,
converted signature bytes and copied source witnesses through a caller-bound
reader. It constructs no protocol body/frame and sends nothing. Its evidence
distinguishes the bounded V2126 offline observation from V2242.4's static-only
five-term compatibility algorithms.

## What accepted1 actually establishes

Observed V2126 receiver `0xBF1924` compares these predicates before sending the
initial live surface state:

| Input | Native comparison |
|---|---|
|123-byte console record|bytes0..26 and0x6F..0x73|
|first ShortString|reference after its first character; the local reference is ASCII-lowercased by the recovered helper|
|state checksum|uint32 sum of native subsystem GetHashTotal results|
|third ShortString|exact Pascal wire length and already-converted signature bytes; not arbitrary product-name text|
|two local mode bytes|reject when both are nonzero|

The second ShortString is retained/copied into peer metadata; this receiver does
not compare it as a compatibility predicate. Arbitrary bytes outside the compared
console slices are not granted structure-setter semantics.

The checksum covers recursive Snapshot/Macro state, with optional subsystem
hashes. A Snapshot count/name fingerprint does not substitute for it. In V2126,
the signature starts from100 codepoints obtained from50 native record pairs; a
special record type masks one bit. The native helper repeatedly converts the
previous ShortString through the target RTL codepage, appends two Unicode
codepoints, then converts back to a Pascal byte string. The resulting wire
signature is not fixed at100 bytes: its ShortString envelope permits0..255 bytes,
and a Windows CP_UTF8 fixture expands the source pair `e9 ff` into `c3 a9 c3 bf`,
producing102 wire bytes with the other98 NUL codepoints. Embedded NUL bytes remain
part of the lengthful string. Its business meaning remains partially unresolved.

**Inference:** accepted1 means the native Session compatibility checks passed and
the peer can proceed with the selected current-state direction. It does not mean
that both peers already hold identical live fader/EQ/mute values: those values
are emitted afterward. A custom client can own the same exported Session and an
independently verified compatibility baseline while its current-value cache is
still awaiting the feed. Simply echoing another peer's checksum/signature is not
independent verification.

`VerifyBaseline` binds the caller's exported Session SHA-256 and evidence ID to
the copied console record, real aggregate native checksum, reference, full
already-converted signature wire bytes,
and local mode observations. `evaluate_session_verify(record, baseline)` reports
the individual predicates and eligibility. It does not obtain those observations,
load a Session, recompute the complete native hash trees, send accepted1, or
prove that the editor will accept the exchange. The `binary_signature` field
takes the exact wire bytes without their one-byte Pascal length prefix; an
independent oracle must supply its `signature_wire_hex`, not its100-byte
`signature_source_codepoints_hex`. The evaluator preserves and compares the full
length and bytes, accepts the structural0..255-byte extent, and never truncates,
pads or chooses a Python encoding. An empty value is structurally valid; that
does not prove normal native signature generation produced it. Its evaluator is
V2126-only.

## Direction and initial state producer

Observed old/new paths:

| Function | V2126 | V2242.4 |
|---|---:|---:|
|SessionVerify send|`0xBF13D8`|`0xC77820`|
|SessionVerify receive|`0xBF1924`|`0xC77D7C`|
|SessionVerified receive|`0xBF1DCC`|`0xC78234`|
|initial current-state emitter|`0xBEF8D4`|`0xC75D18`|
|per-module control visitor|`0xBEF804`|`0xC75C48`|
|native state sender|`0xBF2294`|`0xC786FC`|
|ordinary state batch flush|`0xBF2460`|`0xC788C8`|

- Verify direction0: the Verify receiver emits current surface state after
  successful acceptance.
- Verify direction1: the Verify initiator emits current surface state after it
  receives successful SessionVerified.

The emitter sends a preamble, a native Snapshot-related command, optional
16-byte records, and visits selected native controllers. String controls use
Category8; ordinary numeric states accumulate in12-byte Category9 records.
The selector excludes controls according to native mode, controller definition,
module/parameter cases, and other local options. Inventory membership alone does
not establish that every controller must appear in the initial feed.

The emitter returns after scheduling/visiting; it does not send a proven complete
inventory count, generation token, or end-of-initial-state marker in the inspected
path. Ordinary batch flush also has no verified end marker. TCP idle, EOF, a
SessionVerified reply, or one final-looking Category9 packet must not be used as
proof of a complete mirror replica.

## Exact wire offsets and opaque bytes

The fixed prefix is16 bytes: total length u32@0, local connection index u16@4,
Category byte@6, ControlID5@7, ListIndex i32@12, payload@16, additive checksum at
the final two bytes. Connection indices are local slots and are rewritten by the
receiver, not stable peer identities.

SessionVerify body: direction byte@0, console123@1, aggregate checksum u32@124,
then three sequential Pascal ShortStrings@128. The native builder initializes
packet bytes7..10 and leaves packet byte11 opaque. Do not interpret that last
byte as a meaningful high CommandID byte for this operation.

SessionVerified body is four bytes: accepted@0, direction@1, zero reserved@2..3
in the inspected sender. Unknown boolean/reserved values are rejected by the
passive semantic decoder.

Category9 ordinary-state frame:

| Location | Meaning |
|---|---|
|packet ControlID bytes0..3|DWORD0 for ordinary states, DWORD1 for meter/tally producer|
|packet ControlID byte4|opaque/uninitialized byte11 of the packet|
|body record bytes0..4|raw5-byte control ID|
|body record byte5|opaque; inspected ordinary sender does not initialize it|
|body record bytes6..7|native controller flag word, little-endian|
|body record bytes8..11|four native value bytes; datatype comes from independent inventory|

The ordinary sender flushes when its count exceeds1020, producing1021 records
on that branch. This is an observed implementation threshold, not a universal
protocol maximum. The passive decoder has separate documented local bounds.

The receiver propagates a flag subset masked with `0xE0C0`; bit names and complete
state semantics are not established. The decoder preserves flags. Its raw
observation store excludes records containing other flag bits. Even recognized
bits are not applied to a recreated native flag state machine.

Category0/sub9 is used by the meter/tally path to request another refresh. It is
not a general ACK for ordinary control-state batches. File block ACKs are a
separate Category4 contract. No universal successful-control ACK was established.

## Important V2242.4 differences

Observed new sender/receiver still copy123 console bytes and compare the same
console slices. Initial-state numeric records remain12 bytes and the inspected
Category8/9 sender offsets match V2126. This does not imply interchangeable
identity, Session hashes, runtime object offsets, or complete mode behavior.

V2242.4 adds a mandatory `tSnapshotSetList.GetHashTotal` (`0x9A58E4`) between
Snapshot/Macro and optional hashes. Its object pointer is held via slot
`0xD705E8` → global `0xEB05B8`. The sender and receiver both include it:

| Native component | V2242.4 GetHashTotal |
|---|---:|
|Snapshot list|`0xC9085C`|
|Macros list|`0xB3E43C`|
|Snapshot Setlist list|`0x9A58E4`|
|optional Encoders list|`0x85E658`|
|optional Channel Macros list|`0x95B994`|

Using the old four-component checksum on this build would be incorrect. The
initial Snapshot-related command also changed from directly reading old
Snapshot-list+0x24 to calling `0xC8D988`; that selected-index/Setlist interaction
still needs semantic validation. The new failure/acceptance mode slots are
`0xD70144` and `0xD6F88C`.

The passive semantic module currently rejects the new profile rather than
pretending those unresolved compatibility inputs are supported. These observed
wire similarities are useful research evidence for a separate profile.

## Conservative bounded isolated-lab probe design

1. Use only an isolated copy of the exact V2126 editor with a newly prepared
   synthetic test Session. Preserve the original user Session and process.
   Require no other native peers. Retain before/after Session and INI/mirroring
   observations: mirror mode is not necessarily stored inside the Session.
2. Independently export the already-saved synthetic Session and hash it. Obtain
   stable read-only console123, the full native component-hash sum, the exact
   ASCII reference, binary signature, and local mode flags. If the full hash
   oracle or signature is incomplete, stop at identity/passive observation.
3. Complete normal version/prefix/remote-PC identity with the copied editor;
   collect both directions. This step establishes online state only.
4. Construct SessionVerify direction0 from that independent baseline. Let the
   original editor evaluate it and send its own SessionVerified. The custom
   client does not need to invent an accepted1 reply. Rejection can trigger an
   original failure message, so this requires the disposable lab instance.
5. Collect at most5 seconds,1MiB and4096 frames; these are conservative experiment
   bounds. Decode both reassembled TCP directions with bound inventory types.
   Stop the experiment on unknown semantics, checksum/extent failure, unrelated
   file/write command, or an application error. Retain opaque bytes privately.
6. If accepted1 and expected-direction Category8/9 data appear, report observed
   compatibility acceptance and state feed. Compare a few synthetic sentinel
   control values against fresh read-only inventory. Report coverage without
   claiming initial-sync completion; a reliable end marker is still absent.
7. Close the client normally, wait for peer cleanup, then inspect the copied
   editor, peer table, mirror/mode settings and file hashes. A reconnect creates
   a new local slot/epoch; old acceptance, value cache, ACK position and pending
   Verify state cannot be reused. Never replay uncertain non-idempotent commands.

Possible side effects of successful negotiation include mirror/tally flags,
peer notifications, selected surface/engine association under some role branches,
and outbound current-state/preamble traffic. File requests can additionally Save,
write or Load; rejection may open a native failure dialog. The probe design does
not contact a physical console or establish DSP/audio behavior.

## Passive module use and remaining work

`decode_state_packet` handles known V2126 message shapes. Category8 numeric/string
typing requires an independently bound `control_types={five_byte_id:datatype}`;
otherwise payload remains opaque. Category9 retains the exact raw value bytes.
`MirrorTrace.feed(direction, bytes)` accepts incremental data for
`peer_to_client` and `client_to_peer`, keeps checksum failures permanent within
that epoch, matches Verify/reply direction, and marks disconnect/failure values
stale. `begin_connection()` clears previous values and pending verification.

`snapshot()` intentionally always returns `initial_sync_complete:false` and
`end_marker_verified:false`. It does not send data, apply unknown flags, retry,
reconstruct Snapshot/Setlist/Macro state, or substitute for native readback.

Open work includes public per-build hash-tree/signature oracle integration,
numeric mirror-mode and flag meanings, all initial selector exclusions,
generation/completion detection, file-transfer-to-live-model correspondence,
and real original-endpoint capture validating ordering/reconnect behavior. Static
analysis and synthetic parser tests establish none of those runtime outcomes.
