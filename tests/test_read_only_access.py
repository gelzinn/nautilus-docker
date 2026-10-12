import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nautilus_docker.local_session import LocalDockerSession
from nautilus_docker.read_only_access import ReadOnlyAccess
from nautilus_docker.resources import SECTIONS


class ReadOnlyTests(unittest.TestCase):
    def test_same_user_reads_all_resources_without_any_pkexec(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'access.sock'
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(listener.close)
            listener.bind(str(path))
            listener.listen(1)
            received = []
            def serve():
                connection, _ = listener.accept()
                with connection, connection.makefile('r') as requests:
                    for line in requests:
                        section = json.loads(line)['section']
                        received.append(section)
                        item = {SECTIONS[section]['name_key']: 'demo'}
                        reply = {'ok': True, 'output': json.dumps(item) + '\n'}
                        connection.sendall((json.dumps(reply) + '\n').encode())
            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            session = LocalDockerSession()
            session.read_only = ReadOnlyAccess(path)
            with patch('nautilus_docker.local_session.daemon_running', return_value=True), \
                 patch('nautilus_docker.local_session.local_socket', return_value='unix:///var/run/docker.sock'), \
                 patch('nautilus_docker.local_session.subprocess.Popen') as pkexec:
                for section in [*SECTIONS, 'containers']:
                    self.assertEqual(len(session.query(section)), 1)
                pkexec.assert_not_called()
                session.close()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(received, [*SECTIONS, 'containers'])

    def run_helper(self, peer_uid):
        parent, child = socket.socketpair()
        path = Path(__file__).resolve().parents[1] / 'src/nautilus_docker/local_helper.py'
        process = subprocess.Popen([sys.executable, '-I', str(path), '--socket',
                                    'unix:///var/run/docker.sock', '--peer-uid', str(peer_uid)],
                                   stdin=child, stdout=child, stderr=subprocess.PIPE)
        child.close()
        return parent, process

    def test_socket_service_rejects_mutations_and_exits_on_disconnect(self):
        connection, process = self.run_helper(os.getuid())
        try:
            connection.sendall(b'{"section":"stop"}\n')
            reply = json.loads(connection.recv(4096))
            self.assertFalse(reply['ok'])
            self.assertEqual(reply['kind'], 'format')
        finally:
            connection.close()
            process.wait(timeout=3)
            process.stderr.close()
        self.assertEqual(process.returncode, 0)

    def test_socket_service_rejects_another_uid_before_reading_requests(self):
        connection, process = self.run_helper(os.getuid() + 1)
        try:
            self.assertEqual(process.wait(timeout=3), 1)
        finally:
            connection.close()
            process.stderr.close()

    def test_installer_generates_restricted_per_user_units(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/install_local_access.py'
        spec = importlib.util.spec_from_file_location('local_access_installer', path)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        units = installer.unit_contents(SimpleNamespace(pw_uid=1234, pw_name='demo'))
        self.assertEqual(len(units), 2)
        socket_unit = units['nautilus-docker-readonly-1234.socket']
        service = units['nautilus-docker-readonly-1234@.service']
        self.assertIn('SocketMode=0600', socket_unit)
        self.assertIn('SocketUser=demo', socket_unit)
        self.assertIn('--peer-uid 1234', service)
        self.assertIn('ProtectHome=yes', service)
        self.assertIn('NoNewPrivileges=yes', service)
        self.assertNotIn(str(Path.home()), service)
        with self.assertRaises(ValueError):
            installer.unit_contents(SimpleNamespace(pw_uid=0, pw_name='root'))
