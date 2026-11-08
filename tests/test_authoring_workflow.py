"""Synthetic callback workflow boundaries; never executes a native target."""
import copy
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/digico-native-session/scripts'))
import authoring_workflow as w
import test_authoring as author
import test_control_catalog as vectors


def wrapper(inventory=None, sample='initial'):
    inventory = copy.deepcopy(inventory if inventory is not None else author.with_channels(vectors.inventory()))
    inventory.setdefault('modules', [])
    inventory.update(profile='quantum1-v2126', build_profile='quantum1-v2126',
                     image_base=0x400000, console_pointer=0x900000,
                     structure_verified=True, memory_read_only=True, values_atomic=False,
                     name_encoding='ascii')
    state = {'snapshot_count': 0, 'fps': 25, 'current_session_path': r'C:\Synthetic\Test.ses', 'snapshots': []}
    return {'schema_version': 1, 'status': 'ok', 'process_id': 42,
            'executable_sha256': inventory['executable_sha256'],
            'before': copy.deepcopy(state), 'after': copy.deepcopy(state),
            'error': None, 'inventory': inventory, 'sample_id': sample}


def execution_receipt(raw, current, **extra):
    verified = [{'op': operation['op'], 'verified': True,
                 **{key: operation[key] for key in ('id', 'channel_id', 'value') if key in operation},
                 **({'name_hex': operation['name'].encode(raw['encoding']).hex()} if operation['op'] == 'set_channel_name' else {})}
                for operation in raw['operations']]
    return {'schema_version': 1, 'status': 'ok', 'error': None,
            'process_id': current['process_id'], 'executable_sha256': current['executable_sha256'],
            'before': copy.deepcopy(current['before']), 'after': copy.deepcopy(current['after']),
            'verified_steps': verified, **extra}


class Backend:
    def __init__(self, inventory):
        self.state = copy.deepcopy(inventory)
        self.calls = []
        self.refresh_calls = []
        self.refresh_mutation = None

    def execute(self, raw, current, index):
        self.calls.append((copy.deepcopy(raw), copy.deepcopy(current), index))
        for operation in raw['operations']:
            if operation['op'] != 'set_control':
                continue
            for row in self.state['inventory']['controllers']:
                if row['raw_id_hex'] == operation['id']:
                    row['value_raw_hex'] = struct.pack('<f', operation['value']).hex()
                    if 'output_value_raw_hex' in row:
                        row['output_value_raw_hex'] = row['value_raw_hex']
                    if operation['id'] == '0404000001':
                        for sibling in self.state['inventory']['controllers']:
                            if sibling.get('owner_native_context'):
                                sibling['owner_native_context']['mode_output_raw_hex'] = row['value_raw_hex']
                                sibling['value_raw_hex'] = struct.pack('<f', .6).hex()
        return execution_receipt(raw, current)

    def refresh(self, channel_ids, index):
        self.refresh_calls.append((list(channel_ids), index))
        result = copy.deepcopy(self.state)
        result['sample_id'] = 'fresh-%d' % index
        if self.refresh_mutation:
            self.refresh_mutation(result, index)
        return result


class InventoryMergeTests(unittest.TestCase):
    def test_same_target_union_preserves_response_tables_and_source_hashes_without_mutation(self):
        first = wrapper()
        second = copy.deepcopy(first)
        second['sample_id'] = 'second-filter'
        channel = copy.deepcopy(second['inventory']['channels'][0])
        channel.update(id=257, logical_channel=2, pointer=0x900100, name='Synthetic Other')
        second['inventory']['channels'] = [channel]
        second['inventory']['controllers'][0].update(raw_id_hex='210c000101',
                                                    module_id_hex='000c000101', pointer=0x800100)
        # One filtered source can omit response copies that another source
        # independently carries for the identical shared definition pointer.
        second['inventory']['definitions'][0].pop('response_seed_raw_hex')
        second['inventory']['definitions'][0].pop('response_points')
        original = copy.deepcopy([first, second])
        merged = w.merge_inventory_results([first, second])
        self.assertEqual([row['id'] for row in merged['inventory']['channels']], [256, 257])
        self.assertEqual(len(merged['inventory']['controllers']), 2)
        self.assertEqual(len(merged['inventory']['definitions']), 1)
        self.assertIn('response_seed_raw_hex', merged['inventory']['definitions'][0])
        self.assertIn('response_points', merged['inventory']['definitions'][0])
        self.assertEqual(len(merged['merge_provenance']['sources']), 2)
        self.assertNotEqual(merged['merge_provenance']['sources'][0]['result_sha256'],
                            merged['merge_provenance']['sources'][1]['result_sha256'])
        self.assertFalse(merged['values_atomic'])
        self.assertFalse(merged['inventory']['values_atomic'])
        self.assertEqual([first, second], original)

    def test_every_required_target_and_session_boundary_rejects_cross_merge(self):
        def mutate(document, field):
            if field == 'process_id':
                document[field] += 1
            elif field in ('image_base', 'console_pointer'):
                document['inventory'][field] += 4
            elif field == 'executable_sha256':
                document[field] = document['inventory'][field] = 'b' * 64
            elif field == 'build_profile':
                document['inventory']['profile'] = document['inventory']['build_profile'] = 'quantum-other'
            elif field == 'current_session_path':
                document['inventory']['session_path'] = r'C:\Synthetic\Other.ses'
                document['before'][field] = document['after'][field] = r'C:\Synthetic\Other.ses'
            else:
                document['before'][field] = document['after'][field] = 30 if field == 'fps' else 1
        for field in ('process_id', 'image_base', 'console_pointer', 'executable_sha256',
                      'build_profile', 'current_session_path', 'fps', 'snapshot_count'):
            first, second = wrapper(), wrapper()
            mutate(second, field)
            with self.subTest(field=field), self.assertRaises(w.WorkflowError):
                w.merge_inventory_results([first, second])

    def test_alias_conflicts_missing_provenance_and_during_read_resets_reject(self):
        cases = []
        document = wrapper(); document.pop('process_id'); cases.append(document)
        document = wrapper(); document['inventory'].pop('console_pointer'); cases.append(document)
        document = wrapper(); document['before'].pop('fps'); cases.append(document)
        document = wrapper(); document['after']['snapshot_count'] = 1; cases.append(document)
        document = wrapper(); document['inventory']['process_id'] = 43; cases.append(document)
        document = wrapper(); document['inventory']['structure_verified'] = False; cases.append(document)
        document = wrapper(); document['status'] = 'error'; cases.append(document)
        for document in cases:
            with self.assertRaises(w.WorkflowError):
                w.merge_inventory_results([document])

    def test_duplicate_control_definition_channel_and_module_conflicts_reject(self):
        for collection, field, value in [('controllers', 'value_raw_hex', '00000000'),
                                         ('definitions', 'raw_hex', vectors.definition(name='different')),
                                         ('channels', 'pointer', 0x900004)]:
            first, second = wrapper(), wrapper()
            second['inventory'][collection][0][field] = value
            with self.subTest(collection=collection), self.assertRaises(w.WorkflowError):
                w.merge_inventory_results([first, second])
        first, second = wrapper(), wrapper()
        first['inventory']['modules'] = [{'raw_id_hex': '000c000001', 'pointer': 0x700000}]
        second['inventory']['modules'] = [{'raw_id_hex': '000c000001', 'pointer': 0x700004}]
        with self.assertRaises(w.WorkflowError):
            w.merge_inventory_results([first, second])

    def test_duplicate_native_pointer_with_different_identity_and_unknown_metadata_conflicts_reject(self):
        first, second = wrapper(), wrapper()
        second['inventory']['controllers'][0]['raw_id_hex'] = '210c000101'
        with self.assertRaises(w.WorkflowError):
            w.merge_inventory_results([first, second])
        first, second = wrapper(), wrapper()
        first['inventory']['console_type'] = 27
        second['inventory']['console_type'] = 28
        with self.assertRaises(w.WorkflowError):
            w.merge_inventory_results([first, second])

    def test_same_path_and_count_do_not_hide_changed_snapshot_invariants(self):
        first, second = wrapper(), wrapper()
        second['before']['snapshots'] = second['after']['snapshots'] = [{'name_hex': b'Synthetic Cue'.hex()}]
        with self.assertRaises(w.WorkflowError):
            w.merge_inventory_results([first, second])

    def test_native_object_pointer_cannot_alias_different_collection_types(self):
        document = wrapper()
        document['inventory']['modules'] = [{'raw_id_hex': '000c000001', 'pointer': 0x800000}]
        with self.assertRaises(w.WorkflowError):
            w.merge_inventory_results([document])

    def test_merging_a_previously_merged_result_retains_original_source_lineage(self):
        first = w.merge_inventory_results([wrapper()])
        repeated = w.merge_inventory_results([first])
        original = first['merge_provenance']['sources'][0]['result_sha256']
        upstream = repeated['merge_provenance']['sources'][0]['upstream_merge_provenance']
        self.assertEqual(upstream['sources'][0]['result_sha256'], original)


class WorkflowTests(unittest.TestCase):
    def test_mode_callback_reset_requires_fresh_sibling_expected_and_recompile(self):
        initial = wrapper(author.eq_inventory(1))
        backend = Backend(initial)
        result = w.run_authoring(author.request(author.setting('eq.shape', 4), author.setting('eq.q', .8)),
                                 initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['completed_stages'], 2)
        self.assertEqual(backend.refresh_calls, [([256], 1)])
        self.assertAlmostEqual(backend.calls[1][0]['operations'][0]['expected'], .6)
        self.assertEqual(backend.calls[1][0]['operations'][0]['id'], '0604000001')
        self.assertNotIn('kind', backend.calls[1][0])
        self.assertFalse(result['auto_replay'])

    def test_intermediate_ordinary_mode_and_ordinary_stages_preserve_order(self):
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        document = author.request(author.setting('eq.q', 10), author.setting('eq.shape', 4), author.setting('eq.q', .8))
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([call[2] for call in backend.calls], [0, 1, 2])
        self.assertEqual(backend.refresh_calls, [([256], 1), ([256], 2)])

    def test_illegal_new_shape_value_stops_before_second_execute(self):
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        result = w.run_authoring(author.request(author.setting('eq.shape', 4), author.setting('eq.q', 1)),
                                 initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['failure_phase'], 'recompile')
        self.assertEqual(result['completed_stages'], 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result['remaining_authoring_requests'][0]['operations'][0]['parameter'], 'id:0604000001')

    def test_explicit_stale_expected_is_rejected_before_any_send_and_after_mode_reset(self):
        initial = wrapper(); backend = Backend(initial)
        document = author.request(author.setting(expected={'value': -21, 'unit': 'native'}))
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['error']['code'], 'stale_expected')
        self.assertEqual(backend.calls, [])
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        document = author.request(author.setting('eq.shape', 4),
                                  author.setting('eq.q', .8, expected={'value': .5, 'unit': 'native'}))
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['error']['code'], 'stale_expected')
        self.assertEqual(result['completed_stages'], 1)
        self.assertEqual(len(backend.calls), 1)

    def test_expected_ms_quantization_is_compared_as_native_f32_bytes(self):
        initial = wrapper(author.delay_inventory())
        initial['inventory']['controllers'][0]['value_raw_hex'] = struct.pack('<f', .001).hex()
        backend = Backend(initial)
        document = author.request(author.setting('delay', 5, 'ms', expected={'value': 1, 'unit': 'ms'}))
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'ok')

    def test_ambiguous_initial_name_blocks_all_callbacks(self):
        initial = wrapper(); backend = Backend(initial)
        second = copy.deepcopy(initial['inventory']['channels'][0]); second.update(id=257, logical_channel=2, pointer=0x900100)
        initial['inventory']['channels'].append(second)
        document = author.request(author.setting(channel={'name': 'Synthetic Vocal'}))
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['error']['code'], 'ambiguous_channel')
        self.assertEqual(backend.calls, [])
        self.assertEqual(backend.refresh_calls, [])

    def test_name_selection_is_pinned_before_intermediate_rename_or_duplicate(self):
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        def change_names(document, index):
            first = document['inventory']['channels'][0]
            first.update(name='Synthetic Renamed', name_hex=b'Synthetic Renamed'.hex())
            other = copy.deepcopy(first)
            other.update(id=257, logical_channel=2, pointer=0x900100, name='Synthetic Vocal')
            document['inventory']['channels'].append(other)
        backend.refresh_mutation = change_names
        document = author.request(author.setting('eq.shape', 4, channel={'name': 'Synthetic Vocal'}),
                                  author.setting('eq.q', .8, channel={'name': 'Synthetic Vocal'}))
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(backend.calls[1][0]['operations'][0]['id'], '0604000001')

    def test_process_or_console_reset_at_barrier_stops_without_replay(self):
        for field in ('process_id', 'console_pointer'):
            initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
            def mutate(document, index):
                if field == 'process_id': document[field] += 1
                else: document['inventory'][field] += 4
            backend.refresh_mutation = mutate
            result = w.run_authoring(author.request(author.setting('eq.shape', 4), author.setting('eq.q', .8)),
                                     initial, backend.execute, backend.refresh)
            self.assertEqual(result['error']['code'], 'target_changed')
            self.assertEqual(result['completed_stages'], 1)
            self.assertEqual(len(backend.calls), 1)

    def test_error_partial_receipt_retains_verified_prefix_and_never_replays(self):
        initial = wrapper(); backend = Backend(initial)
        def partial(raw, current, index):
            backend.calls.append((copy.deepcopy(raw), current, index))
            receipt = execution_receipt(raw, current, status='error', error='synthetic second-step failure')
            receipt['verified_steps'] = receipt['verified_steps'][:1]
            return receipt
        result = w.run_authoring(author.request(author.setting(value=-10), author.setting(value=-5)),
                                 initial, partial, backend.refresh)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['verified_operation_count'], 1)
        self.assertEqual(result['completed_stages'], 0)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(len(result['stages'][0]['execution_result']['verified_steps']), 1)

    def test_unknown_status_incomplete_success_wrong_readback_or_exception_are_indeterminate(self):
        for kind in ('unknown', 'incomplete', 'wrong_id', 'wrong_value', 'exception', 'missing_state', 'non_json'):
            initial = wrapper(); calls = []
            def callback(raw, current, index):
                calls.append(index)
                if kind == 'exception': raise TimeoutError('synthetic timeout after possible send')
                receipt = execution_receipt(raw, current)
                if kind == 'unknown': receipt['status'] = 'unknown'
                elif kind == 'incomplete': receipt['verified_steps'] = []
                elif kind == 'wrong_id': receipt['verified_steps'][0]['id'] = '210c000101'
                elif kind == 'wrong_value': receipt['verified_steps'][0]['value'] = -5
                elif kind == 'missing_state': receipt.pop('after')
                elif kind == 'non_json': receipt['invalid_extra'] = object()
                return receipt
            result = w.run_authoring(author.request(author.setting()), initial, callback, lambda ids, index: initial)
            self.assertEqual(result['status'], 'indeterminate', kind)
            self.assertEqual(calls, [0])
            self.assertEqual(result['completed_stages'], 0)

    def test_explicit_indeterminate_native_operation_always_stops(self):
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        def uncertain(raw, current, index):
            backend.calls.append((raw, current, index))
            return execution_receipt(raw, current, status='error',
                                     indeterminate_operation={'op': 'set_control', 'reason': 'synthetic disconnect'})
        result = w.run_authoring(author.request(author.setting('eq.shape', 4), author.setting('eq.q', .8)),
                                 initial, uncertain, backend.refresh)
        self.assertEqual(result['status'], 'indeterminate')
        self.assertEqual(backend.refresh_calls, [])

    def test_final_mode_refresh_checks_target_without_reexecuting_mode(self):
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        result = w.run_authoring(author.request(author.setting('eq.shape', 4)), initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(backend.refresh_calls, [([256], 1)])
        self.assertIn('final_inventory_binding', result)
        self.assertEqual(len(backend.calls), 1)

    def test_final_refresh_failure_preserves_completed_execution_receipt(self):
        initial = wrapper(author.eq_inventory(1)); backend = Backend(initial)
        def failed(ids, index): raise OSError('synthetic inventory read failure')
        result = w.run_authoring(author.request(author.setting('eq.shape', 4)), initial, backend.execute, failed)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['failure_phase'], 'final_refresh')
        self.assertEqual(result['completed_stages'], 1)
        self.assertEqual(result['stages'][0]['status'], 'ok')
        self.assertEqual(len(backend.calls), 1)

    def test_callbacks_cannot_mutate_callers_original_request_or_inventory(self):
        initial = wrapper(); document = author.request(author.setting())
        originals = copy.deepcopy((document, initial))
        def mutate(raw, current, index):
            receipt = execution_receipt(raw, current)
            raw['operations'].clear(); current['inventory']['channels'].clear()
            return receipt
        result = w.run_authoring(document, initial, mutate, lambda ids, index: initial)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual((document, initial), originals)

    def test_cp950_rename_receipt_must_match_the_requested_name_bytes(self):
        initial = wrapper(author.add_name_controller(author.with_channels(vectors.inventory())))
        document = author.request({'op': 'rename', 'channel': {'id': 256}, 'name': '合成通道'}, encoding='cp950')
        backend = Backend(initial)
        result = w.run_authoring(document, initial, backend.execute, backend.refresh)
        self.assertEqual(result['status'], 'ok')
        def wrong_name(raw, current, index):
            result = execution_receipt(raw, current)
            result['verified_steps'][0]['name_hex'] = b'wrong'.hex()
            return result
        result = w.run_authoring(document, initial, wrong_name, backend.refresh)
        self.assertEqual(result['status'], 'indeterminate')


if __name__ == '__main__':
    unittest.main()
