"""Optional local read-only access through a user-specific Unix socket."""

import json
import os
from pathlib import Path
import socket

from .docker import QueryError, parse_items


def access_socket():
    return Path('/run/nautilus-docker') / f'{os.getuid()}.sock'


class ReadOnlyAccess:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else access_socket()
        self.connection = None
        self.buffer = b''

    def connect(self):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(35)
        try:
            connection.connect(str(self.path))
        except OSError:
            connection.close()
            raise
        self.connection = connection

    def query(self, section):
        if self.connection is None:
            self.connect()
        try:
            self.connection.sendall((json.dumps({'section': section}) + '\n').encode())
            while b'\n' not in self.buffer:
                chunk = self.connection.recv(65536)
                if not chunk:
                    raise QueryError('Local read-only access disconnected.', 'connection')
                self.buffer += chunk
                if len(self.buffer) > 32 * 1024 * 1024:
                    raise QueryError('The local Docker response is too large.', 'format')
            line, self.buffer = self.buffer.split(b'\n', 1)
            reply = json.loads(line)
            if not isinstance(reply, dict) or type(reply.get('ok')) is not bool:
                raise QueryError('Invalid local read-only response.', 'format')
            if not reply['ok']:
                raise QueryError(reply.get('error') or 'Could not query Docker.', reply.get('kind', 'connection'))
            if not isinstance(reply.get('output'), str):
                raise QueryError('Invalid local read-only response.', 'format')
            return parse_items(reply['output'], section)
        except socket.timeout as error:
            self.close()
            raise QueryError('The local read-only query timed out.', 'timeout') from error
        except (OSError, ValueError) as error:
            self.close()
            raise QueryError(str(error), 'connection') from error
        except QueryError:
            self.close()
            raise

    def close(self):
        connection, self.connection = self.connection, None
        self.buffer = b''
        if connection is not None:
            connection.close()
