"""Crash-prefix and recovery contracts, with no editor or network."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skills/digico-native-session/scripts'))
from operation_journal import read_journal, recovery_report, JournalError


def journal_bytes(events, request_hash='ab'*32):
    events=[('request_started',{'request_sha256':request_hash,'mode':'execute'})]+events
    prior='0'*64;lines=[]
    for n,(event,data) in enumerate(events):
        payload=json.dumps(dict(schema_version=1,sequence=n,previous_sha256=prior,event=event,data=data),ensure_ascii=False,separators=(',',':'))
        prior=hashlib.sha256(payload.encode()).hexdigest()
        lines.append(json.dumps(dict(payload_json=payload,sha256=prior),ensure_ascii=False).encode()+b'\n')
    return b''.join(lines)


class JournalTests(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'receipts.jsonl';p.write_bytes(data);return read_journal(p)

    def test_written_packet_is_still_indeterminate_without_readback(self):
        j=self.read(journal_bytes([('step_started',{'operation_index':0,'operation':{'op':'set_control'}}),('packet_send_returned',{'operation_index':0,'acceptance_verified':False})]))
        r=recovery_report(j)
        self.assertEqual(r['indeterminate_operation_indices'],[0])
        self.assertEqual(r['verified_operation_indices'],[])
        self.assertFalse(r['automatic_replay_allowed']);self.assertTrue(r['fresh_state_required'])

    def test_verified_prefix_survives_torn_next_receipt(self):
        raw=journal_bytes([('step_started',{'operation_index':0}),('step_verified',{'operation_index':0,'receipt':{'verified':True,'op':'set_control'}}),('step_started',{'operation_index':1})])
        j=self.read(raw+b'{"payload_json":')
        r=recovery_report(j);self.assertTrue(r['tail_incomplete'])
        self.assertEqual(r['verified_operation_indices'],[0]);self.assertEqual(r['indeterminate_operation_indices'],[1])

    def test_even_valid_unterminated_tail_has_no_authority(self):
        raw=journal_bytes([('request_finished',{'status':'ok'})]);j=self.read(raw.rstrip(b'\n'))
        self.assertTrue(j['tail_incomplete']);self.assertEqual(recovery_report(j)['request_outcome'],'not_recorded')

    def test_complete_corrupt_record_and_digest_tamper_fail(self):
        raw=journal_bytes([])
        for data in (raw+b'broken\n',raw.replace(b'execute',b'execUTE')):
            with self.assertRaises(JournalError):self.read(data)

    def test_sequence_chain_splice_and_duplicate_fields_fail(self):
        raw=journal_bytes([('step_started',{'operation_index':0})]);lines=raw.splitlines(keepends=True)
        for data in (lines[0]+lines[0], lines[1]+lines[0], b'{"payload_json":"x","payload_json":"y","sha256":"z"}\n'):
            with self.assertRaises(JournalError):self.read(data)

    def test_mismatched_request_and_orphan_verified_receipt_fail(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'compiled.json';p.write_bytes(b'{}')
            with self.assertRaises(JournalError):recovery_report(self.read(journal_bytes([])),compiled_request_path=p)
        for events in [[('step_verified',{'operation_index':0,'receipt':{'verified':True}})], [('request_finished',{'status':'ok'}),('target_bound',{})]]:
            with self.assertRaises(JournalError):recovery_report(self.read(journal_bytes(events)))

    def test_matching_request_and_completed_recovery_are_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'compiled.json';p.write_bytes(b'{"synthetic":true}')
            sha=hashlib.sha256(p.read_bytes()).hexdigest();r=recovery_report(self.read(journal_bytes([('request_finished',{'status':'ok'})],sha)),compiled_request_path=p)
        self.assertEqual(r['request_outcome'],'ok');self.assertFalse(r['target_called']);self.assertFalse(r['network_used'])


if __name__=='__main__':unittest.main()
