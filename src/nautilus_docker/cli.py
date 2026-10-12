"""Manage the same SSH connections as the desktop without loading GTK."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

from .connections import ConfigurationError, Connections, new_connection, resolve_match_hosts


def add_commands(parser):
    commands = parser.add_subparsers(dest="command")
    connections = commands.add_parser("connections", help="Manage saved SSH connections")
    actions = connections.add_subparsers(dest="connection_action", required=True)

    listing = actions.add_parser("list", help="List local and saved SSH connections")
    listing.add_argument("--json", action="store_true", dest="connection_json")

    show = actions.add_parser("show", help="Show a connection's settings")
    show.add_argument("connection_id", metavar="ID")
    show.add_argument("--json", action="store_true", dest="connection_json")

    adding = actions.add_parser("add", help="Add an SSH hostname, IP address or alias")
    adding.add_argument("connection_id", metavar="ID", help="Stable ID used with the desktop's --host option")
    adding.add_argument("connection_host", metavar="HOST", help="Hostname, IP address, SSH alias or user@host")
    adding.add_argument("--name", dest="connection_name", metavar="NAME", help="Display name (default: ID)")
    adding.add_argument("--user", dest="connection_user", metavar="USER", help="SSH username")
    adding.add_argument("--port", type=int, dest="connection_port", metavar="PORT", help="SSH port")
    adding.add_argument("--identity", dest="connection_identity", metavar="FILE", help="SSH private key path")
    adding.add_argument("--sudo", action="store_true", dest="connection_sudo", help="Allow the desktop to use sudo for Docker")

    editing = actions.add_parser("edit", help="Update a saved SSH connection")
    editing.add_argument("connection_id", metavar="ID")
    editing.add_argument("--name", dest="connection_name", metavar="NAME")
    editing.add_argument("--host", dest="connection_host", metavar="HOST")
    editing.add_argument("--user", dest="connection_user", metavar="USER", help="SSH username; pass an empty string to clear")
    ports = editing.add_mutually_exclusive_group()
    ports.add_argument("--port", type=int, dest="connection_port", metavar="PORT")
    ports.add_argument("--default-port", action="store_true", dest="connection_default_port", help="Use the SSH configuration's port")
    editing.add_argument("--identity", dest="connection_identity", metavar="FILE", help="SSH key path; pass an empty string to clear")
    sudo = editing.add_mutually_exclusive_group()
    sudo.add_argument("--sudo", action="store_true", dest="connection_sudo")
    sudo.add_argument("--no-sudo", action="store_false", dest="connection_sudo")
    editing.set_defaults(connection_sudo=None)

    removing = actions.add_parser("remove", help="Remove a saved SSH connection")
    removing.add_argument("connection_id", metavar="ID")


def _target(host, username, previous_username=""):
    host = host.strip()
    username = username.strip() if username is not None else None
    if "@" in host:
        embedded, host = host.rsplit("@", 1)
        embedded = embedded.strip()
        if username is not None and username != embedded:
            raise ConfigurationError("The username in HOST conflicts with --user.")
        username = embedded
    return host, previous_username if username is None else username


def _connection(options, previous=None):
    host, username = _target(options.connection_host if options.connection_host is not None else previous.host,
                             options.connection_user, previous.username if previous else "")
    label = options.connection_name
    if label is None:
        label = previous.label if previous else options.connection_id
    port = options.connection_port
    if previous and port is None and not options.connection_default_port:
        port = previous.port
    identity = options.connection_identity
    if identity is None:
        identity = previous.identity_file if previous else ""
    elif identity.strip():
        try:
            identity = str(Path(identity.strip()).expanduser().absolute())
        except RuntimeError as error:
            raise ConfigurationError(str(error)) from error
    sudo = options.connection_sudo
    if sudo is None:
        sudo = previous.sudo if previous else False
    connection = new_connection(label, host, username=username, port=port,
                                identity_file=identity, sudo=sudo, connection_id=options.connection_id)
    matches = resolve_match_hosts(connection)
    if previous and connection.host == previous.host:
        matches = list(dict.fromkeys([*matches, *previous.match_hosts]))
    return replace(connection, match_hosts=matches)


def _show(connection):
    fields = (("ID", connection.id), ("Name", connection.label),
              ("Host", connection.host or "(local Docker)"),
              ("User", connection.username or "(none)"),
              ("Port", connection.port if connection.port is not None else "(SSH configuration)"),
              ("Identity", connection.identity_file or "(none)"),
              ("Sudo", "yes" if connection.sudo else "no"),
              ("Match hosts", ", ".join(connection.match_hosts) or "(none)"))
    for label, value in fields:
        print(f"{label}: {value}")


def run(options, store=None):
    store = store if store is not None else Connections()
    try:
        connections = store.load()
        action = options.connection_action
        if action == "list":
            ordered = [connections["local"], *(connections[key] for key in sorted(connections) if key != "local")]
            if options.connection_json:
                print(json.dumps([asdict(connection) for connection in ordered], indent=2, ensure_ascii=False))
            else:
                print("ID\tNAME\tTARGET")
                for connection in ordered:
                    print(f"{connection.id}\t{connection.label}\t{connection.description}")
        elif action == "show":
            if options.connection_id not in connections:
                raise ConfigurationError(f"Unknown connection ID: {options.connection_id}")
            connection = connections[options.connection_id]
            if options.connection_json:
                print(json.dumps(asdict(connection), indent=2, ensure_ascii=False))
            else:
                _show(connection)
        else:
            connection_id = options.connection_id
            if not connection_id:
                raise ConfigurationError("Invalid connection ID.")
            if connection_id in ("local", "all"):
                raise ConfigurationError(f"The connection ID '{connection_id}' is reserved.")
            if action == "add":
                if connection_id in connections:
                    raise ConfigurationError(f"Connection ID already exists: {connection_id}")
                store.save(_connection(options), create_only=True)
                print(f"Added connection {connection_id}.")
            else:
                if connection_id not in connections:
                    raise ConfigurationError(f"Unknown connection ID: {connection_id}")
                if action == "edit":
                    store.save(_connection(options, connections[connection_id]), expected=connections[connection_id])
                    print(f"Updated connection {connection_id}.")
                elif action == "remove":
                    store.remove(connection_id)
                    print(f"Removed connection {connection_id}.")
        return 0
    except (ConfigurationError, OSError) as error:
        print(f"nautilus-docker: {error}", file=sys.stderr)
        return 1
