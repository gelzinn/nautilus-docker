"""Standalone privileged helper: four fixed Docker list operations over pipes.

Run with Python's isolated mode. Never import the checkout's resource schemas or
accept command arguments from requests. EOF releases the authorized session.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket as sockets
import struct
import subprocess
import sys

COMMANDS = {
    "containers": ("container", "ls", "--all", "--no-trunc"),
    "images": ("image", "ls", "--all", "--no-trunc"),
    "volumes": ("volume", "ls"),
    "networks": ("network", "ls", "--no-trunc"),
}


def daemon_running():
    try:
        for process in Path("/proc").iterdir():
            if process.name.isdigit():
                try:
                    if (process / "comm").read_text().strip() == "dockerd":
                        return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def handle_request(request, socket):
    if (not isinstance(request, dict) or set(request) != {"section"}
            or not isinstance(request["section"], str) or request["section"] not in COMMANDS):
        return {"ok": False, "error": "Unsupported resource query.", "kind": "format"}
    if not daemon_running():
        return {"ok": False, "error": "Local Docker is not running.", "kind": "inactive"}
    docker = shutil.which("docker", path=os.defpath + ":/usr/local/bin")
    if not docker:
        return {"ok": False, "error": "The Docker CLI is not installed.", "kind": "connection"}
    command = [docker, "--host", socket, *COMMANDS[request["section"]], "--format", "{{json .}}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=25,
                                stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "The Docker query timed out.", "kind": "timeout"}
    except OSError as error:
        return {"ok": False, "error": str(error), "kind": "connection"}
    if result.returncode:
        return {"ok": False, "error": (result.stderr or result.stdout).strip()[:1600], "kind": "connection"}
    return {"ok": True, "output": result.stdout}


def main():
    parser = argparse.ArgumentParser(description="Nautilus Docker read-only local session")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--peer-uid", type=int, help="Require this Unix socket peer UID (systemd mode)")
    args = parser.parse_args()
    if not args.socket.startswith("unix:///"):
        parser.error("Only an absolute local Unix socket is supported.")
    if args.peer_uid is not None and not authorized_peer(sys.stdin.fileno(), args.peer_uid):
        return 1
    while True:
        line = sys.stdin.buffer.readline(4096)
        if not line:
            return 0
        if not line.endswith(b"\n"):
            return 2
        try:
            reply = handle_request(json.loads(line), args.socket)
        except (ValueError, TypeError):
            reply = {"ok": False, "error": "Invalid resource query.", "kind": "format"}
        try:
            print(json.dumps(reply), flush=True)
        except BrokenPipeError:
            return 0


def authorized_peer(fd, allowed_uid):
    """Use kernel credentials; ignore identity fields supplied by clients."""
    try:
        with sockets.fromfd(fd, sockets.AF_UNIX, sockets.SOCK_STREAM) as connection:
            _pid, uid, _gid = struct.unpack('3i', connection.getsockopt(sockets.SOL_SOCKET, sockets.SO_PEERCRED, 12))
            return uid == allowed_uid
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
