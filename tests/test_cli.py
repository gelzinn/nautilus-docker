from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nautilus_docker.app import parser
from nautilus_docker.cli import run
from nautilus_docker.connections import Connections, LOCAL, new_connection


class ConnectionCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "settings/connections.json"
        self.store = Connections(self.path)
        self.resolve_patch = patch("nautilus_docker.cli.resolve_match_hosts",
                                   side_effect=lambda connection: [connection.host, "resolved.example"])
        self.resolve = self.resolve_patch.start()
        self.addCleanup(self.resolve_patch.stop)

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = run(parser().parse_args(["connections", *arguments]), self.store)
        return status, stdout.getvalue(), stderr.getvalue()

    def saved(self):
        connection = new_connection("My server", "alice@alias", port=2222,
                                    identity_file="/tmp/key with spaces", sudo=True, connection_id="demo")
        self.store.save(replace(connection, match_hosts=["alias", "previous.example"]))
        return self.store.load()["demo"]

    def test_list_and_show_builtin_local_without_creating_configuration(self):
        status, stdout, stderr = self.cli("list", "--json")
        self.assertEqual((status, stderr), (0, ""))
        self.assertEqual(json.loads(stdout), [asdict(LOCAL)])
        self.assertEqual(json.loads(self.cli("show", "local", "--json")[1]), asdict(LOCAL))
        self.assertIn("local\tLocal\tLocal Docker", self.cli("list")[1])
        self.assertFalse(self.path.exists())
        self.resolve.assert_not_called()

    def test_add_all_settings_and_private_configuration(self):
        status, stdout, stderr = self.cli("add", "demo", "alice@alias", "--name", "My server",
                                           "--user", "alice", "--port", "2222", "--identity",
                                           "/tmp/key with spaces", "--sudo")
        self.assertEqual((status, stderr), (0, ""))
        self.assertIn("Added connection demo", stdout)
        connection = self.store.load()["demo"]
        self.assertEqual((connection.label, connection.target, connection.port, connection.identity_file,
                          connection.sudo, connection.match_hosts),
                         ("My server", "alice@alias", 2222, "/tmp/key with spaces", True,
                          ["alias", "resolved.example"]))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.resolve.assert_called_once()

    def test_default_name_and_root_host_option_do_not_override_target(self):
        options = parser().parse_args(["--host", "gui-host", "connections", "add", "demo", "server.example"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(run(options, self.store), 0)
        connection = self.store.load()["demo"]
        self.assertEqual((options.host, connection.host, connection.label), ("gui-host", "server.example", "demo"))

    def test_list_is_stable_and_show_includes_every_setting(self):
        original = self.saved()
        self.store.save(new_connection("Other server", "host.example", connection_id="aaa"))
        rows = json.loads(self.cli("list", "--json")[1])
        self.assertEqual([row["id"] for row in rows], ["local", "aaa", "demo"])
        self.assertEqual(rows[2], asdict(original))
        self.assertIn("demo\tMy server\talice@alias:2222", self.cli("list")[1])
        self.assertEqual(json.loads(self.cli("show", "demo", "--json")[1]), asdict(original))
        details = self.cli("show", "demo")[1]
        for value in ("ID: demo", "Name: My server", "Host: alias", "User: alice", "Port: 2222",
                      "Identity: /tmp/key with spaces", "Sudo: yes", "Match hosts: alias, previous.example"):
            self.assertIn(value, details)
        self.resolve.assert_not_called()

    def test_edit_preserves_omitted_fields_and_existing_matching_hosts(self):
        original = self.saved()
        self.assertEqual(self.cli("edit", "demo", "--name", "Renamed")[0], 0)
        expected = replace(original, label="Renamed", match_hosts=["alias", "resolved.example", "previous.example"])
        self.assertEqual(self.store.load()["demo"], expected)
        self.resolve.assert_called_once()

    def test_edit_can_clear_user_key_port_and_sudo(self):
        self.saved()
        self.assertEqual(self.cli("edit", "demo", "--user", "", "--identity", "",
                                  "--default-port", "--no-sudo")[0], 0)
        connection = self.store.load()["demo"]
        self.assertEqual((connection.username, connection.identity_file, connection.port, connection.sudo),
                         ("", "", None, False))
        self.assertEqual(self.cli("edit", "demo", "--sudo", "--port", "22")[0], 0)
        self.assertTrue(self.store.load()["demo"].sudo)
        self.assertEqual(self.store.load()["demo"].port, 22)

    def test_changed_host_discards_stale_matches_and_keeps_omitted_username(self):
        self.saved()
        self.assertEqual(self.cli("edit", "demo", "--host", "new.example")[0], 0)
        connection = self.store.load()["demo"]
        self.assertEqual((connection.host, connection.username, connection.match_hosts),
                         ("new.example", "alice", ["new.example", "resolved.example"]))

    def test_embedded_new_username_overrides_old_username(self):
        self.saved()
        self.assertEqual(self.cli("edit", "demo", "--host", "bob@alias")[0], 0)
        self.assertEqual(self.store.load()["demo"].username, "bob")
        self.assertEqual(self.cli("edit", "demo", "--host", "carol@other", "--user", "carol")[0], 0)
        self.assertEqual(self.store.load()["demo"].target, "carol@other")

    def test_conflicting_embedded_and_explicit_username_never_changes_configuration(self):
        self.saved()
        original = self.path.read_bytes()
        for action in [("add", "other", "alice@host", "--user", "bob"),
                       ("edit", "demo", "--host", "bob@alias", "--user", "alice"),
                       ("edit", "demo", "--host", "bob@alias", "--user", "")]:
            with self.subTest(action=action):
                status, _, stderr = self.cli(*action)
                self.assertEqual(status, 1)
                self.assertIn("conflicts with --user", stderr)
                self.assertEqual(self.path.read_bytes(), original)
        self.resolve.assert_not_called()

    def test_duplicate_reserved_and_unknown_ids_cannot_mutate_configuration(self):
        self.saved()
        original = self.path.read_bytes()
        cases = [("add", "demo", "other.example")]
        for connection_id in ("local", "all"):
            cases.extend([("add", connection_id, "other.example"), ("edit", connection_id),
                          ("remove", connection_id)])
        cases.extend((action, "missing") for action in ("show", "edit", "remove"))
        for arguments in cases:
            with self.subTest(arguments=arguments):
                status, stdout, stderr = self.cli(*arguments)
                self.assertEqual((status, stdout), (1, ""))
                self.assertTrue(stderr.startswith("nautilus-docker: "), stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertEqual(self.path.read_bytes(), original)
        self.resolve.assert_not_called()

    def test_an_add_during_alias_resolution_is_not_overwritten(self):
        concurrent = new_connection("Concurrent", "second.example", connection_id="demo")

        def resolving(connection):
            self.store.save(concurrent)
            return [connection.host]

        self.resolve.side_effect = resolving
        status, stdout, stderr = self.cli("add", "demo", "first.example")
        self.assertEqual((status, stdout), (1, ""))
        self.assertIn("already exists", stderr)
        self.assertEqual(self.store.load()["demo"], concurrent)

    def test_a_stale_edit_neither_overwrites_nor_resurrects_a_connection(self):
        original = self.saved()
        concurrent = replace(original, label="Concurrent edit")
        for operation in (lambda: self.store.save(concurrent), lambda: self.store.remove("demo")):
            with self.subTest(operation=operation):
                self.store.save(original)
                expected = {}

                def resolving(connection):
                    operation()
                    expected.update(self.store.load())
                    return [connection.host]

                self.resolve.side_effect = resolving
                status, stdout, stderr = self.cli("edit", "demo", "--name", "Stale edit")
                self.assertEqual((status, stdout), (1, ""))
                self.assertTrue(stderr.startswith("nautilus-docker: "), stderr)
                self.assertEqual(self.store.load(), expected)

    def test_explicit_identity_paths_are_anchored_without_resolving_symlinks(self):
        working = Path(self.temporary.name) / "working"
        working.mkdir()
        target = working / "private-key"
        target.write_text("test fixture")
        (working / "key-link").symlink_to(target)
        with patch("os.getcwd", return_value=str(working)):
            self.assertEqual(self.cli("add", "demo", "host", "--identity", "key-link")[0], 0)
        self.assertEqual(self.store.load()["demo"].identity_file, str(working / "key-link"))
        other_directory = Path(self.temporary.name) / "elsewhere"
        other_directory.mkdir()
        with patch("os.getcwd", return_value=str(other_directory)):
            self.assertEqual(self.cli("edit", "demo", "--name", "Renamed")[0], 0)
        self.assertEqual(self.store.load()["demo"].identity_file, str(working / "key-link"))
        with patch("os.getcwd", return_value=str(other_directory)):
            self.assertEqual(self.cli("edit", "demo", "--identity", "different-key")[0], 0)
        self.assertEqual(self.store.load()["demo"].identity_file, str(other_directory / "different-key"))
        self.assertEqual(self.cli("edit", "demo", "--identity", "~/key")[0], 0)
        self.assertEqual(self.store.load()["demo"].identity_file, str(Path.home() / "key"))

    def test_omitted_legacy_relative_key_is_preserved_and_unknown_user_home_fails_cleanly(self):
        connection = new_connection("Legacy", "host", identity_file="relative-key", connection_id="demo")
        self.store.save(connection)
        self.assertEqual(self.cli("edit", "demo", "--name", "Renamed")[0], 0)
        self.assertEqual(self.store.load()["demo"].identity_file, "relative-key")
        with patch("nautilus_docker.cli.Path.expanduser", side_effect=RuntimeError("Could not determine home directory")):
            status, stdout, stderr = self.cli("edit", "demo", "--identity", "~missing-user/key")
        self.assertEqual((status, stdout), (1, ""))
        self.assertIn("Could not determine home directory", stderr)
        self.assertEqual(self.store.load()["demo"].identity_file, "relative-key")

    def test_invalid_connection_values_are_rejected_before_alias_resolution(self):
        cases = [("add", "", "host"), ("add", "bad.id", "host"), ("add", "demo", "-bad"),
                 ("add", "demo", "host;command"), ("add", "demo", "host", "--name", ""),
                 ("add", "demo", "host", "--user", "bad user"),
                 ("add", "demo", "host", "--port", "0"),
                 ("add", "demo", "host", "--port", "65536")]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                # A leading dash in a positional host needs argparse's end-of-options marker.
                if arguments[-1] == "-bad":
                    arguments = ("add", "demo", "--", "-bad")
                status, _, stderr = self.cli(*arguments)
                self.assertEqual(status, 1)
                self.assertTrue(stderr.startswith("nautilus-docker: "), stderr)
                self.assertFalse(self.path.exists())
        self.resolve.assert_not_called()

    def test_corrupt_configuration_is_retained_for_every_action(self):
        self.path.parent.mkdir()
        self.path.write_text("{invalid json")
        original = self.path.read_bytes()
        for arguments in [("list",), ("show", "local"), ("add", "demo", "alias"),
                          ("edit", "demo"), ("remove", "demo")]:
            with self.subTest(arguments=arguments):
                status, stdout, stderr = self.cli(*arguments)
                self.assertEqual((status, stdout), (1, ""))
                self.assertIn("Could not load connections", stderr)
                self.assertEqual(self.path.read_bytes(), original)
        self.resolve.assert_not_called()

    def test_remove_and_write_errors_have_clear_exit_statuses(self):
        self.saved()
        with patch.object(self.store, "save", side_effect=OSError("permission denied")):
            status, stdout, stderr = self.cli("edit", "demo", "--name", "New")
        self.assertEqual((status, stdout, stderr), (1, "", "nautilus-docker: permission denied\n"))
        self.assertEqual(self.cli("remove", "demo")[0], 0)
        self.assertEqual(self.store.load(), {"local": LOCAL})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_alias_resolution_uses_only_openssh_configuration(self):
        self.resolve_patch.stop()
        with patch("nautilus_docker.connections.subprocess.run") as ssh:
            ssh.return_value = subprocess.CompletedProcess([], 0, "hostname server.example\n", "")
            self.assertEqual(self.cli("add", "demo", "alice@alias", "--port", "2222")[0], 0)
        self.assertEqual(ssh.call_args.args[0], ["ssh", "-G", "-p", "2222", "alice@alias"])
        self.assertEqual(self.store.load()["demo"].match_hosts, ["alias", "server.example"])

    def test_usage_errors_are_argparse_errors(self):
        for arguments in [("connections",), ("connections", "edit", "demo", "--port", "bad"),
                          ("connections", "edit", "demo", "--port", "22", "--default-port"),
                          ("connections", "edit", "demo", "--sudo", "--no-sudo"),
                          ("docker", "rm", "demo"), ("connections", "docker", "rm")]:
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    parser().parse_args(arguments)
                self.assertEqual(failure.exception.code, 2)


class SourceLauncherCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.launcher = Path(__file__).resolve().parents[1] / "bin/nautilus-docker"
        (self.root / "sitecustomize.py").write_text(
            "import sys\n"
            "class RejectGtk:\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname == 'gi' or fullname.startswith('gi.'):\n"
            "            raise ModuleNotFoundError('GTK disabled by CLI test')\n"
            "sys.meta_path.insert(0, RejectGtk())\n")
        self.ssh_log = self.root / "ssh-arguments.json"
        ssh = self.root / "ssh"
        ssh.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                       "from pathlib import Path\n"
                       "Path(os.environ['TEST_SSH_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
                       "print('hostname server.example')\n")
        ssh.chmod(0o755)
        self.environment = {**os.environ, "XDG_CONFIG_HOME": str(self.root / "config"),
                            "DISPLAY": "", "WAYLAND_DISPLAY": "", "PYTHONPATH": str(self.root),
                            "PATH": str(self.root) + os.pathsep + os.defpath,
                            "TEST_SSH_LOG": str(self.ssh_log)}

    def cli(self, *arguments):
        return subprocess.run([sys.executable, str(self.launcher), *arguments], env=self.environment,
                              capture_output=True, text=True, timeout=5)

    def test_source_launcher_manages_connections_without_display_or_gtk(self):
        guard = subprocess.run([sys.executable, "-c", "import gi"], env=self.environment,
                               capture_output=True, text=True, timeout=5)
        self.assertNotEqual(guard.returncode, 0)
        self.assertIn("GTK disabled by CLI test", guard.stderr)
        for arguments in [("--help",), ("--version",), ("connections", "list", "--json"),
                          ("connections", "add", "demo", "alice@alias", "--port", "2222"),
                          ("connections", "edit", "demo", "--name", "New name"),
                          ("connections", "show", "demo", "--json"), ("connections", "remove", "demo")]:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                if arguments[:2] == ("connections", "show"):
                    self.assertEqual(json.loads(result.stdout)["label"], "New name")
        self.assertEqual(json.loads(self.ssh_log.read_text()), ["-G", "-p", "2222", "alice@alias"])
        self.assertEqual(json.loads(self.cli("connections", "list", "--json").stdout), [asdict(LOCAL)])

    def test_source_launcher_rejects_unknown_commands_and_preserves_bad_config(self):
        for arguments in [("containers", "rm", "demo"), ("connections", "docker", "rm"),
                          ("connections", "add", "demo", "host", "--port", "abc")]:
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("Traceback", result.stderr)
        path = self.root / "config/nautilus-docker/connections.json"
        path.parent.mkdir(parents=True)
        path.write_text("invalid configuration")
        result = self.cli("connections", "add", "demo", "host")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Could not load connections", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(path.read_text(), "invalid configuration")
        self.assertFalse(self.ssh_log.exists())


if __name__ == "__main__":
    unittest.main()
