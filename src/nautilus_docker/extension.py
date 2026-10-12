"""Nautilus menus; no network or Docker queries run inside GNOME Files."""

import shutil
import subprocess
import gi
try:
    gi.require_version("Nautilus", "4.1")
except ValueError:
    gi.require_version("Nautilus", "4.0")
from gi.repository import GObject, Nautilus
from .connections import Connections, ConfigurationError, connection_for_uri
from .resources import SECTIONS


def launch(host="local", section="containers", manage=False):
    executable = shutil.which("nautilus-docker")
    if not executable:
        # The source installer does not depend on the desktop session's PATH.
        from pathlib import Path
        executable = str(Path.home() / ".local/bin/nautilus-docker")
    arguments = [executable, "--host", host, "--section", section]
    if manage:
        arguments.append("--connections")
    subprocess.Popen(arguments, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DockerMenuProvider(GObject.GObject, Nautilus.MenuProvider):
    def menus(self, uris):
        try:
            connections = Connections().load()
        except ConfigurationError:
            connections = {}
        hosts = {connection_for_uri(uri, connections) for uri in uris}
        host = hosts.pop() if len(hosts) == 1 else "all"
        if host is None:
            item = Nautilus.MenuItem(name="NautilusDocker::Configure", label="Configure Docker connection…",
                                     tip="Add an SSH connection for this server")
            item.connect("activate", lambda *_: launch(manage=True))
            return [item]
        label = "Docker" if host == "all" else "Docker · " + (connections[host].label if host in connections else "Local")
        root = Nautilus.MenuItem(name="NautilusDocker::Root", label=label, tip="Browse Docker resources")
        menu = Nautilus.Menu()
        for section, spec in SECTIONS.items():
            item = Nautilus.MenuItem(name="NautilusDocker::" + section, label=spec["label"], tip="Browse " + spec["plural"])
            item.connect("activate", lambda _, key=section: launch(host, key))
            menu.append_item(item)
        manage = Nautilus.MenuItem(name="NautilusDocker::Connections", label="Connections…", tip="Manage SSH connections")
        manage.connect("activate", lambda *_: launch(manage=True))
        menu.append_item(manage)
        root.set_submenu(menu)
        return [root]

    def get_background_items(self, current_folder):
        return self.menus([current_folder.get_uri()])

    def get_file_items(self, files):
        return self.menus([file.get_uri() for file in files]) if files else []
