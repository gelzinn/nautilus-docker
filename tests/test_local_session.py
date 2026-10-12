import json
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from nautilus_docker import local_helper
from nautilus_docker.local_session import LocalDockerSession
from nautilus_docker.docker import QueryError
from nautilus_docker.resources import SECTIONS


class HelperTests(unittest.TestCase):
    @patch('nautilus_docker.local_helper.subprocess.run')
    def test_arbitrary_commands_and_extra_arguments_are_rejected(self, run):
        for request in ({'section': 'stop'}, {'section': 'containers', 'args': ['rm']},
                        {'section': []}, {}, ['containers'], {'command': 'sh'}):
            with self.subTest(request=request):
                self.assertFalse(local_helper.handle_request(request, 'unix:///var/run/docker.sock')['ok'])
        run.assert_not_called()

    @patch('nautilus_docker.local_helper.subprocess.run')
    @patch('nautilus_docker.local_helper.daemon_running', return_value=False)
    def test_helper_does_not_activate_a_stopped_daemon(self, running, run):
        result = local_helper.handle_request({'section': 'containers'}, 'unix:///var/run/docker.sock')
        self.assertEqual(result['kind'], 'inactive')
        run.assert_not_called()

    @patch('nautilus_docker.local_helper.subprocess.run')
    @patch('nautilus_docker.local_helper.shutil.which', return_value='/usr/bin/docker')
    @patch('nautilus_docker.local_helper.daemon_running', return_value=True)
    def test_privileged_commands_match_only_the_four_list_views(self, running, which, run):
        run.return_value = subprocess.CompletedProcess([], 0, '', '')
        for section, spec in SECTIONS.items():
            self.assertTrue(local_helper.handle_request({'section': section}, 'unix:///var/run/docker.sock')['ok'])
            self.assertEqual(run.call_args.args[0],
                             ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock',
                              *spec['command'], '--format', '{{json .}}'])


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = patch('nautilus_docker.read_only_access.access_socket', return_value=Path(temporary.name) / 'absent.sock')
        path.start()
        self.addCleanup(path.stop)

    def helper_process(self, code=None):
        # A real pipe protocol, simulated without pkexec or Docker.
        original_popen = subprocess.Popen
        code = code or '''import json,sys
for line in sys.stdin:
    section=json.loads(line)['section']
    data={'Names':section,'Name':section,'Repository':section,'ID':section}
    print(json.dumps({'ok':True,'output':json.dumps(data)+'\\n'}),flush=True)
'''
        processes = []
        def spawn(*args, **kwargs):
            process = original_popen([sys.executable, '-I', '-u', '-c', code], **kwargs)
            processes.append(process)
            return process
        mock = patch('nautilus_docker.local_session.subprocess.Popen', side_effect=spawn)
        return mock, processes

    @patch('nautilus_docker.local_session.daemon_running', return_value=True)
    def test_one_authorization_serves_all_views_and_refreshes(self, running):
        factory, processes = self.helper_process()
        with factory as launch:
            session = LocalDockerSession()
            try:
                for section in [*SECTIONS, 'containers', 'images']:
                    items = session.query(section, authorize=section == 'containers')
                    self.assertEqual(len(items), 1)
                self.assertEqual(launch.call_count, 1)
                self.assertEqual(launch.call_args.args[0][0], 'pkexec')
                self.assertIn('-I', launch.call_args.args[0])
                self.assertTrue(session.active)
            finally:
                session.close()
            processes[0].wait(timeout=3)
            self.assertFalse(session.active)
            with self.assertRaises(QueryError):
                session.query('images', authorize=True)

    @patch('nautilus_docker.local_session.daemon_running', return_value=True)
    @patch('nautilus_docker.local_session.list_items', return_value=[])
    def test_regular_access_never_opens_an_authorization_prompt(self, normal, running):
        with patch('nautilus_docker.local_session.subprocess.Popen') as launch:
            session = LocalDockerSession()
            self.assertEqual(session.query('images'), [])
            launch.assert_not_called()
            session.close()

    @patch('nautilus_docker.local_session.daemon_running', return_value=True)
    def test_cancelled_authentication_is_retryable(self, running):
        factory, processes = self.helper_process('import sys; sys.exit(126)')
        with factory as launch:
            session = LocalDockerSession()
            try:
                with self.assertRaises(QueryError) as error:
                    session.query('containers', authorize=True)
                self.assertEqual(error.exception.kind, 'permission')
                self.assertFalse(session.active)
                with self.assertRaises(QueryError):
                    session.query('containers', authorize=True)
                self.assertEqual(launch.call_count, 2)
            finally:
                session.close()
            for process in processes:
                process.wait(timeout=3)

    @patch('nautilus_docker.local_session.daemon_running', return_value=True)
    def test_malformed_docker_json_is_reported(self, running):
        factory, processes = self.helper_process("import sys,json\nfor line in sys.stdin: print(json.dumps({'ok':True,'output':'not JSON'}),flush=True)")
        with factory:
            session = LocalDockerSession()
            try:
                with self.assertRaises(QueryError) as error:
                    session.query('containers', authorize=True)
                self.assertEqual(error.exception.kind, 'format')
            finally:
                session.close()
            processes[0].wait(timeout=3)

    @patch('nautilus_docker.local_session.daemon_running', return_value=True)
    def test_closing_a_pending_session_unblocks_the_query(self, running):
        factory, processes = self.helper_process('import sys,time; sys.stdin.readline(); time.sleep(30)')
        with factory:
            session = LocalDockerSession()
            errors = []
            def query():
                try:
                    session.query('containers', authorize=True)
                except QueryError as error:
                    errors.append(error)
            worker = threading.Thread(target=query, daemon=True)
            worker.start()
            deadline = time.monotonic() + 2
            while not processes and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(processes)
            session.close()
            worker.join(timeout=3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors[0].kind, 'permission')
            processes[0].wait(timeout=3)

    @patch('nautilus_docker.local_session.daemon_running', return_value=True)
    def test_pipe_eof_ends_helper_when_client_cannot_signal_it(self, running):
        factory, processes = self.helper_process()
        with factory:
            session = LocalDockerSession()
            session.query('containers', authorize=True)
            with patch.object(processes[0], 'terminate', side_effect=PermissionError):
                session.close()
                self.assertEqual(processes[0].wait(timeout=3), 0)
