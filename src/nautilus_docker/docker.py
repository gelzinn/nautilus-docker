"""Read-only resource queries, using the Docker CLI locally or through SSH."""

from pathlib import Path
import shlex
import shutil
import subprocess

from .resources import SECTIONS


class QueryError(Exception):
    def __init__(self, message, kind="connection"):
        super().__init__(message)
        self.kind = kind


def daemon_running():
    """Avoid activating docker.socket as a side effect of browsing resources."""
    try:
        processes = Path("/proc").iterdir()
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                if (process / "comm").read_text().strip() == "dockerd":
                    return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def local_socket():
    import os
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and (Path(runtime) / "docker.sock").exists():
        return f"unix://{runtime}/docker.sock"
    return "unix:///var/run/docker.sock"


def command_for(connection, section="containers", use_sudo=False):
    arguments = [*SECTIONS[section]["command"], "--format", "{{json .}}"]
    if not connection.local:
        command = ["ssh", "-oBatchMode=yes", "-oStrictHostKeyChecking=accept-new",
                   "-oConnectTimeout=8", "-oServerAliveInterval=5", "-oServerAliveCountMax=2"]
        if connection.port:
            command += ["-p", str(connection.port)]
        if connection.identity_file:
            command += ["-i", str(Path(connection.identity_file).expanduser())]
        docker = ["docker", *arguments]
        if use_sudo:
            docker = ["sudo", "-n", "docker", *arguments]
        return [*command, connection.target, shlex.join(docker)]
    docker = [shutil.which("docker") or "docker", "--host", local_socket(), *arguments]
    return docker


def parse_items(output, section="containers"):
    import json
    items = []
    for line in output.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise QueryError("Docker returned an unexpected response.", "format")
        items.append({key: str(item[key]) if item.get(key) is not None else ""
                      for key in SECTIONS[section]["fields"]})
    name_key = SECTIONS[section]["name_key"]
    return sorted(items, key=lambda item: (item[name_key].casefold(), item.get("Tag", "")))


def list_items(connection, section="containers", authorize=False, local_session=None):
    if connection.local and local_session is not None:
        return local_session.query(section, authorize)
    if connection.local and authorize:
        raise QueryError("Local authorization requires a window session.", "permission")
    try:
        if connection.local and not daemon_running():
            raise QueryError("Local Docker is not running.", "inactive")
        result = subprocess.run(command_for(connection, section), capture_output=True,
                                text=True, timeout=25)
        permission = "permission denied" in result.stderr.lower() and "docker.sock" in result.stderr
        if not connection.local and result.returncode and permission and connection.sudo:
            result = subprocess.run(command_for(connection, section, use_sudo=True),
                                    capture_output=True, text=True, timeout=25)
        if result.returncode:
            error = (result.stderr or result.stdout).strip()
            kind = "permission" if connection.local and permission else "connection"
            raise QueryError(error[:1600] or "Could not query Docker.", kind)
        return parse_items(result.stdout, section)
    except subprocess.TimeoutExpired as error:
        raise QueryError("The query timed out. Try refreshing again.", "timeout") from error
    except (OSError, ValueError) as error:
        raise QueryError(str(error)) from error
