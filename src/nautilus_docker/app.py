"""Application entry point; desktop styling is inherited from GTK."""

import argparse
import sys

from . import __version__
from .cli import add_commands, run as run_cli
from .resources import SECTIONS, stylesheet


def parser():
    result = argparse.ArgumentParser(description="Nautilus Docker resource browser")
    result.add_argument("--host", default="local", help="Connection ID or 'all' (default: local)")
    result.add_argument("--section", choices=SECTIONS, default="containers")
    result.add_argument("--connections", action="store_true", help="Manage SSH connections")
    result.add_argument("--version", action="version", version=__version__)
    add_commands(result)
    return result


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    options = parser().parse_args(arguments)
    if options.command == "connections":
        return run_cli(options)
    # Help, version and connection management work without GTK or a display.
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, Gtk
    from .connections import Connections, ConfigurationError
    from .window import DockerWindow

    class DockerApplication(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.nautilusdocker.App",
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.window = None
            self.connections = Connections()
            self.selected, self.section = "local", "containers"

        def do_startup(self):
            Adw.Application.do_startup(self)
            provider = Gtk.CssProvider()
            provider.load_from_string(stylesheet())
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                       Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        def do_command_line(self, command_line):
            try:
                options = parser().parse_args(command_line.get_arguments()[1:])
                try:
                    known = self.connections.load()
                except ConfigurationError:
                    known = {"local": None}
                if options.host != "all" and options.host not in known:
                    print("Unknown connection ID. Use 'connections list' or --connections to manage SSH connections.", file=sys.stderr)
                    return 2
                self.selected, self.section = options.host, options.section
                self.activate()
                if options.connections:
                    self.window.manage_connections()
                return 0
            except SystemExit as error:
                return error.code

        def do_activate(self):
            if self.window is None or self.window.closed:
                self.window = DockerWindow(self, self.connections, self.selected, self.section)
            else:
                # Another launcher invocation can arrive after connections were edited.
                try:
                    changed = self.window.connections != self.connections.load()
                except ConfigurationError:
                    changed = True
                if changed:
                    self.window.connections_changed(self.selected)
                self.window.select_view(self.selected, self.section)
            self.window.present()

        def do_shutdown(self):
            if self.window is not None:
                self.window.local_session.close()
            Adw.Application.do_shutdown(self)

    return DockerApplication().run(["nautilus-docker", *arguments])
