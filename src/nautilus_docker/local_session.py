"""A window-scoped read-only helper, authorized once through PolicyKit."""

import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import threading
import time

from .connections import LOCAL
from .docker import QueryError, daemon_running, list_items, local_socket, parse_items
from .resources import SECTIONS
from .read_only_access import ReadOnlyAccess


class LocalDockerSession:
    def __init__(self):
        self.process = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.buffer = b""
        self.read_only = ReadOnlyAccess()

    @property
    def active(self):
        return not self.closed.is_set() and self.process is not None and self.process.poll() is None

    def query(self, section, authorize=False):
        if section not in SECTIONS:
            raise QueryError("Unsupported resource query.", "format")
        with self.lock:
            if self.closed.is_set():
                raise QueryError("The local authorization session has closed.", "permission")
            if not daemon_running():
                raise QueryError("Local Docker is not running.", "inactive")
            if local_socket() == 'unix:///var/run/docker.sock' and (self.read_only.connection or self.read_only.path.exists()):
                try:
                    return self.read_only.query(section)
                finally:
                    if self.closed.is_set():
                        self.read_only.close()
            if not self.active:
                self._release()
                if not authorize:
                    return list_items(LOCAL, section)
                python = shutil.which("python3", path=os.defpath)
                if not python:
                    raise QueryError("The system Python interpreter is not installed.")
                helper = Path(__file__).with_name("local_helper.py")
                try:
                    process = subprocess.Popen(["pkexec", python, "-I", str(helper), "--socket", local_socket()],
                                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                               stderr=subprocess.DEVNULL, bufsize=0)
                except OSError as error:
                    raise QueryError(str(error), "permission") from error
                self.process = process
                # A close request can arrive while the process is being created.
                if self.closed.is_set():
                    self._release()
                    raise QueryError("The local authorization session has closed.", "permission")
            try:
                payload = (json.dumps({"section": section}) + "\n").encode()
                self.process.stdin.write(payload)
                reply = self._read_reply(125 if authorize else 35)
            except (OSError, ValueError, QueryError) as error:
                self._release()
                if isinstance(error, QueryError):
                    raise
                raise QueryError("Local authorization ended. Click Authorize to try again.", "permission") from error
            if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
                self._release()
                raise QueryError("The local helper returned an invalid response.", "format")
            if not reply["ok"]:
                raise QueryError(reply.get("error") or "Could not query local Docker.", reply.get("kind", "connection"))
            output = reply.get("output")
            if not isinstance(output, str):
                raise QueryError("The local helper returned an invalid response.", "format")
            try:
                return parse_items(output, section)
            except (ValueError, TypeError) as error:
                raise QueryError("Docker returned an invalid response.", "format") from error

    def _read_reply(self, timeout):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                if self.closed.is_set():
                    raise QueryError("The local authorization session has closed.", "permission")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise QueryError("Local authorization timed out. Click Authorize to try again.", "permission")
                if not selector.select(min(remaining, .25)):
                    continue
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    raise QueryError("Local authorization was cancelled or ended. Click Authorize to try again.", "permission")
                self.buffer += chunk
                if len(self.buffer) > 32 * 1024 * 1024:
                    raise QueryError("The local Docker response is too large.", "format")
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def _release(self):
        process, self.process = self.process, None
        self.buffer = b""
        if process is None:
            return
        # Closing stdin makes the privileged helper exit, even after a parent crash.
        # A user process cannot signal a helper that has already switched to root.
        for stream in (process.stdin, process.stdout):
            if stream:
                stream.close()
        try:
            process.terminate()  # Dismiss pending authentication when still owned by the user.
        except OSError:
            pass  # An authorized root helper exits through EOF instead.
        def reap():
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                try:
                    process.terminate()  # pkexec may still be waiting for authentication.
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        threading.Thread(target=reap, daemon=True).start()

    def close(self):
        self.closed.set()
        # Keep GUI close requests responsive while authorization is pending.
        if self.lock.acquire(blocking=False):
            try:
                self.read_only.close()
                self._release()
            finally:
                self.lock.release()
