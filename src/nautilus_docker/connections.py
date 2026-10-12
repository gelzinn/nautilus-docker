"""Per-user SSH connections. No connection data belongs in the source tree."""

from dataclasses import asdict, dataclass, field
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
import uuid


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Connection:
    id: str
    label: str
    host: str = ""
    username: str = ""
    port: int | None = None
    identity_file: str = ""
    sudo: bool = False
    match_hosts: list[str] = field(default_factory=list)

    @property
    def local(self):
        return self.id == "local"

    @property
    def target(self):
        return f"{self.username}@{self.host}" if self.username else self.host

    @property
    def description(self):
        return "Local Docker" if self.local else self.target + (f":{self.port}" if self.port else "")

    def validate(self):
        if any(not isinstance(value, str) for value in (self.id, self.label, self.host, self.username, self.identity_file)):
            raise ConfigurationError("Connection fields must be strings.")
        if type(self.sudo) is not bool:
            raise ConfigurationError("The sudo setting must be a boolean.")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", self.id) or self.id in ("local", "all"):
            raise ConfigurationError("Invalid connection ID.")
        if not self.label.strip():
            raise ConfigurationError("Enter a name for the connection.")
        if not re.fullmatch(r"[a-zA-Z0-9:][a-zA-Z0-9._:-]*", self.host):
            raise ConfigurationError("Enter a valid hostname, IP address or SSH alias.")
        if self.username and not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", self.username):
            raise ConfigurationError("Invalid SSH username.")
        if self.port is not None and (type(self.port) is not int or not 1 <= self.port <= 65535):
            raise ConfigurationError("The port must be between 1 and 65535.")
        if not isinstance(self.match_hosts, list) or any(not isinstance(h, str) for h in self.match_hosts):
            raise ConfigurationError("Invalid matching hostnames.")


LOCAL = Connection(id="local", label="Local")


def config_path():
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "nautilus-docker/connections.json"


class Connections:
    def __init__(self, path=None):
        self.path = Path(path) if path else config_path()

    def load(self):
        if not self.path.exists():
            return {"local": LOCAL}
        try:
            payload = json.loads(self.path.read_text())
            if payload.get("version") != 1 or not isinstance(payload.get("connections"), list):
                raise ConfigurationError("Invalid connection configuration format.")
            connections = {"local": LOCAL}
            for record in payload["connections"]:
                connection = Connection(**record)
                connection.validate()
                if connection.id in connections:
                    raise ConfigurationError("Duplicate connection IDs.")
                connections[connection.id] = connection
            return connections
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise ConfigurationError(f"Could not load connections: {error}") from error

    def save(self, connection, *, create_only=False, expected=None):
        connection.validate()
        with self._locked():
            connections = self.load()
            if create_only and connection.id in connections:
                raise ConfigurationError(f"Connection ID already exists: {connection.id}")
            if expected is not None and connections.get(connection.id) != expected:
                raise ConfigurationError("Connection changed or was removed. Reload it and try again.")
            connections[connection.id] = connection
            self._write(connections)

    def remove(self, connection_id):
        if connection_id != "local":
            with self._locked():
                connections = self.load()
                connections.pop(connection_id, None)
                self._write(connections)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self.path.with_name(self.path.name + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        # Keep the lock file: unlinking it would let writers lock different inodes.

    def _write(self, connections):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {"version": 1, "connections": [asdict(c) for c in connections.values() if not c.local]}
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False) as temporary:
            temporary.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            temporary_path = Path(temporary.name)
        try:
            temporary_path.chmod(0o600)
            temporary_path.replace(self.path)
        finally:
            temporary_path.unlink(missing_ok=True)


def new_connection(label, host, username="", port=None, identity_file="", sudo=False, connection_id=None):
    host = host.strip()
    if "@" in host and not username:
        username, host = host.rsplit("@", 1)
    connection = Connection(id=connection_id or uuid.uuid4().hex[:12], label=label.strip(),
                            host=host, username=username.strip(), port=port,
                            identity_file=identity_file.strip(), sudo=bool(sudo), match_hosts=[host])
    connection.validate()
    return connection


def resolve_match_hosts(connection):
    """Read OpenSSH's effective hostname without contacting the server."""
    command = ["ssh", "-G"]
    if connection.port:
        command += ["-p", str(connection.port)]
    command.append(connection.target)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3)
        hostname = next((line.split(maxsplit=1)[1] for line in result.stdout.splitlines()
                         if line.startswith("hostname ")), None) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        hostname = None
    return list(dict.fromkeys([connection.host, *connection.match_hosts, *([hostname] if hostname else [])]))


def connection_for_uri(uri, connections):
    try:
        parsed = urlsplit(uri)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme == "file":
        return "local"
    if parsed.scheme not in ("sftp", "ssh"):
        return "all"
    for connection in connections.values():
        if connection.local:
            continue
        if hostname in [connection.host.lower(), *(h.lower() for h in connection.match_hosts)]:
            if port and connection.port and port != connection.port:
                continue
            if parsed.username and connection.username and parsed.username != connection.username:
                continue
            return connection.id
    return None
