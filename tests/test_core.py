import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from nautilus_docker.connections import (Connections, ConfigurationError, LOCAL, new_connection,
                                         connection_for_uri, resolve_match_hosts)
from nautilus_docker.docker import command_for, list_items, parse_items, QueryError
from nautilus_docker.resources import SECTIONS


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings/connections.json'
        self.store = Connections(self.path)

    def test_new_install_has_only_local(self):
        self.assertEqual(list(self.store.load()), ['local'])
        self.assertFalse(self.path.exists())

    def test_save_edit_remove_and_private_permissions(self):
        self.store.save(new_connection('Demo', 'alice@server.example', port=2222, connection_id='demo'))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.store.load()['demo'].target, 'alice@server.example')
        self.store.save(new_connection('Changed', 'other.example', connection_id='demo'))
        self.assertEqual(len(self.store.load()), 2)
        self.assertEqual(self.store.load()['demo'].host, 'other.example')
        self.store.remove('demo')
        self.assertEqual(list(self.store.load()), ['local'])

    def test_invalid_ports_and_ssh_targets(self):
        for port in (0, 65536, True, '22'):
            with self.subTest(port=port), self.assertRaises(ConfigurationError):
                new_connection('Server', 'server.example', port=port)
        for host in ('-oProxyCommand=bad', 'host;touch /tmp/file', '', 'space host'):
            with self.subTest(host=host), self.assertRaises(ConfigurationError):
                new_connection('Server', host)

    def test_bad_config_is_not_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_text('{"version": 1, "connections": [{"id": "bad", "host": 42, "label": "Bad"}]}')
        original = self.path.read_text()
        with self.assertRaises(ConfigurationError):
            self.store.save(new_connection('Demo', 'server.example'))
        self.assertEqual(self.path.read_text(), original)

    def test_duplicate_ids_rejected(self):
        self.store.save(new_connection('Demo', 'server.example', connection_id='demo'))
        payload = json.loads(self.path.read_text())
        payload['connections'] *= 2
        self.path.write_text(json.dumps(payload))
        with self.assertRaises(ConfigurationError):
            self.store.load()

    def test_context_matching_is_scoped(self):
        self.store.save(new_connection('Demo', 'server.example', username='alice', port=2222, connection_id='demo'))
        hosts = self.store.load()
        self.assertEqual(connection_for_uri('sftp://alice@server.example:2222/home/alice', hosts), 'demo')
        self.assertIsNone(connection_for_uri('sftp://bob@server.example:2222/', hosts))
        self.assertIsNone(connection_for_uri('sftp://alice@server.example:2223/', hosts))
        self.assertIsNone(connection_for_uri('sftp://unknown.example/', hosts))
        self.assertEqual(connection_for_uri('file:///tmp', hosts), 'local')
        self.assertEqual(connection_for_uri('network:///', hosts), 'all')
        self.assertIsNone(connection_for_uri('sftp://[invalid/', hosts))

    @patch('nautilus_docker.connections.subprocess.run')
    def test_alias_resolution_never_connects(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = 'hostname server.example\nuser alice\n'
        self.assertEqual(resolve_match_hosts(new_connection('Demo', 'demo')), ['demo', 'server.example'])
        self.assertEqual(run.call_args.args[0], ['ssh', '-G', 'demo'])


class QueryTests(unittest.TestCase):
    def test_each_resource_uses_only_a_list_command(self):
        connection = new_connection('Demo', 'server.example')
        for section, spec in SECTIONS.items():
            with self.subTest(section=section):
                remote = shlex.split(command_for(connection, section)[-1])
                self.assertEqual(remote, ['docker', *spec['command'], '--format', '{{json .}}'])
                self.assertEqual(remote[2], 'ls')

    def test_ssh_quoting_and_connection_options(self):
        c = new_connection('Demo', 'alice@server.example', port=2222, identity_file='/tmp/key with spaces')
        cmd = command_for(c, 'volumes', use_sudo=True)
        self.assertIn('/tmp/key with spaces', cmd)
        self.assertEqual(cmd[-2], 'alice@server.example')
        self.assertEqual(shlex.split(cmd[-1]), ['sudo', '-n', 'docker', 'volume', 'ls', '--format', '{{json .}}'])
        self.assertIn('-oBatchMode=yes', cmd)
        self.assertIn('-oStrictHostKeyChecking=accept-new', cmd)

    def test_parse_filters_sensitive_fields_and_preserves_false(self):
        output = '\n'.join(json.dumps(item) for item in [
            {'Name': 'z', 'ID': '2', 'Internal': False, 'Env': ['SECRET=x']},
            {'Name': 'a', 'ID': '1', 'Internal': True}])
        items = parse_items(output, 'networks')
        self.assertEqual([item['Name'] for item in items], ['a', 'z'])
        self.assertEqual(items[1]['Internal'], 'False')
        self.assertNotIn('Env', items[1])

    @patch('nautilus_docker.docker.subprocess.run')
    @patch('nautilus_docker.docker.daemon_running', return_value=False)
    def test_stopped_daemon_is_not_socket_activated(self, running, run):
        with self.assertRaises(QueryError) as error:
            list_items(LOCAL)
        self.assertEqual(error.exception.kind, 'inactive')
        run.assert_not_called()

    @patch('nautilus_docker.docker.subprocess.run')
    def test_remote_sudo_is_opt_in_and_only_on_socket_denial(self, run):
        denied = subprocess.CompletedProcess([], 1, '', 'permission denied: /var/run/docker.sock')
        success = subprocess.CompletedProcess([], 0, '', '')
        run.side_effect = [denied, success]
        self.assertEqual(list_items(new_connection('Demo', 'server.example', sudo=True)), [])
        self.assertEqual(run.call_count, 2)
        self.assertEqual(shlex.split(run.call_args.args[0][-1])[:3], ['sudo', '-n', 'docker'])
        run.reset_mock()
        run.side_effect = [denied]
        with self.assertRaises(QueryError):
            list_items(new_connection('Demo', 'server.example', sudo=False))
        self.assertEqual(run.call_count, 1)

    @patch('nautilus_docker.docker.subprocess.run')
    @patch('nautilus_docker.docker.daemon_running', return_value=True)
    def test_local_denial_offers_permission_error(self, running, run):
        run.return_value = subprocess.CompletedProcess([], 1, '', 'permission denied: /var/run/docker.sock')
        with self.assertRaises(QueryError) as error:
            list_items(LOCAL)
        self.assertEqual(error.exception.kind, 'permission')

    @patch('nautilus_docker.docker.subprocess.run', side_effect=subprocess.TimeoutExpired('ssh', 25))
    def test_timeout_is_reported(self, run):
        with self.assertRaises(QueryError) as error:
            list_items(new_connection('Demo', 'server.example'))
        self.assertEqual(error.exception.kind, 'timeout')
