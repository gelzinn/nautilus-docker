from contextlib import contextmanager
from dataclasses import replace
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from nautilus_docker.connections import Connections, ConfigurationError, new_connection


def mutate(path, action, connection, attempted, writing, release, results):
    class ControlledConnections(Connections):
        @contextmanager
        def _locked(self):
            attempted.set()
            with super()._locked():
                yield

        def _write(self, connections):
            writing.set()
            if release is not None and not release.wait(5):
                raise TimeoutError('Writer was not released')
            super()._write(connections)

    try:
        store = ControlledConnections(path)
        if action == 'remove':
            store.remove(connection.id)
        else:
            store.save(connection, create_only=action == 'create')
        results.put(('ok', ''))
    except ConfigurationError as error:
        results.put(('conflict', str(error)))
    except Exception as error:
        results.put(('error', repr(error)))


class ConnectionConcurrencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'settings/connections.json'
        self.store = Connections(self.path)
        self.context = multiprocessing.get_context('fork')

    def race(self, first_action, first, second_action, second):
        release = self.context.Event()
        attempts = [self.context.Event(), self.context.Event()]
        writes = [self.context.Event(), self.context.Event()]
        results = [self.context.Queue(), self.context.Queue()]
        workers = [self.context.Process(target=mutate, args=(
            self.path, action, connection, attempts[index], writes[index],
            release if index == 0 else None, results[index]))
            for index, (action, connection) in enumerate([
                (first_action, first), (second_action, second)])]
        try:
            workers[0].start()
            self.assertTrue(writes[0].wait(5), 'First writer did not reach its write')
            workers[1].start()
            self.assertTrue(attempts[1].wait(5), 'Second writer did not attempt mutation')
            self.assertFalse(writes[1].wait(0.2), 'Second writer bypassed the held lock')
            release.set()
            for worker in workers:
                worker.join(5)
                self.assertEqual(worker.exitcode, 0)
            return [result.get(timeout=1) for result in results]
        finally:
            release.set()
            for worker in workers:
                if worker.pid is not None:
                    if worker.is_alive():
                        worker.terminate()
                    worker.join(5)
            for result in results:
                result.close()
                result.join_thread()

    def test_independent_process_saves_preserve_both_connections(self):
        first = new_connection('First', 'first.example', connection_id='first')
        second = new_connection('Second', 'second.example', connection_id='second')
        self.assertEqual(self.race('save', first, 'save', second), [('ok', ''), ('ok', '')])
        self.assertEqual(self.store.load()['first'], first)
        self.assertEqual(self.store.load()['second'], second)

    def test_concurrent_create_only_rejects_duplicate_without_overwriting(self):
        first = new_connection('First', 'first.example', connection_id='shared')
        second = replace(first, label='Second', host='second.example')
        outcomes = self.race('create', first, 'create', second)
        self.assertEqual(outcomes[0], ('ok', ''))
        self.assertEqual(outcomes[1][0], 'conflict')
        self.assertIn('already exists', outcomes[1][1])
        self.assertEqual(self.store.load()['shared'], first)

    def test_stale_edits_reject_changed_or_removed_connection(self):
        original = new_connection('Original', 'original.example', connection_id='server')
        other_store = Connections(self.path)
        for mutation in ('change', 'remove'):
            with self.subTest(mutation=mutation):
                self.store.save(original)
                snapshot = self.store.load()['server']
                if mutation == 'change':
                    other_store.save(replace(original, label='Updated'))
                else:
                    other_store.remove('server')
                before = self.path.read_bytes()
                with self.assertRaisesRegex(ConfigurationError, 'changed or was removed'):
                    self.store.save(replace(original, label='Stale edit'), expected=snapshot)
                self.assertEqual(self.path.read_bytes(), before)

    def test_save_remove_race_does_not_resurrect_removed_connection(self):
        removed = new_connection('Remove', 'remove.example', connection_id='removed')
        kept = new_connection('Keep', 'keep.example', connection_id='kept')
        added = new_connection('Add', 'add.example', connection_id='added')
        self.store.save(removed)
        self.store.save(kept)
        self.assertEqual(self.race('save', added, 'remove', removed), [('ok', ''), ('ok', '')])
        self.assertEqual(set(self.store.load()), {'local', 'kept', 'added'})
        self.assertEqual(self.store.load()['kept'], kept)
        self.assertEqual(self.store.load()['added'], added)

    def test_lock_is_private_and_keeps_its_inode_across_mutations(self):
        connection = new_connection('Demo', 'demo.example', connection_id='demo')
        self.store.save(connection)
        lock_path = self.path.with_name(self.path.name + '.lock')
        self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)
        inode = lock_path.stat().st_ino
        lock_path.chmod(0o666)
        Connections(self.path).save(replace(connection, label='Changed'), expected=connection)
        self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)
        self.store.remove(connection.id)
        self.assertEqual(lock_path.stat().st_ino, inode)
        self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
