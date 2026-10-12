"""Native dialogs for adding and managing external Docker connections."""

from dataclasses import replace
import threading

from gi.repository import Adw, GLib, Gtk

from .connections import ConfigurationError, new_connection, resolve_match_hosts


class ConnectionEditor(Adw.Dialog):
    def __init__(self, store, changed, connection=None):
        super().__init__(title="Edit connection" if connection else "New SSH connection", content_width=470)
        self.store, self.changed, self.connection = store, changed, connection
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=18, margin_bottom=20, margin_start=20, margin_end=20)
        self.entries = {}
        for key, title, placeholder in (("label", "Name", "Server"),
                                         ("host", "Host or SSH alias", "server.example or SSH alias"),
                                         ("username", "Username", "Use SSH configuration"),
                                         ("port", "Port", "Use SSH configuration (default: 22)"),
                                         ("identity_file", "SSH key", "Use SSH agent or configuration")):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            box.append(Gtk.Label(label=title, xalign=0))
            entry = Gtk.Entry(placeholder_text=placeholder, hexpand=True)
            if connection:
                value = getattr(connection, key)
                entry.set_text(str(value) if value is not None else "")
            self.entries[key] = entry
            if key == "identity_file":
                row = Gtk.Box(spacing=8)
                row.append(entry)
                browse = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Choose SSH key")
                browse.connect("clicked", self.choose_key)
                row.append(browse)
                box.append(row)
            else:
                box.append(entry)
            body.append(box)
        self.sudo = Gtk.CheckButton(label="Use sudo when Docker requires it")
        self.sudo.set_active(connection.sudo if connection else False)
        body.append(self.sudo)
        self.error = Gtk.Label(xalign=0, wrap=True)
        self.error.add_css_class("error")
        body.append(self.error)
        self.save_button = Gtk.Button(label="Save connection", halign=Gtk.Align.END)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", self.save)
        body.append(self.save_button)
        toolbar.set_content(body)
        self.set_child(toolbar)

    def choose_key(self, *_):
        chooser = Gtk.FileChooserNative.new("Choose SSH key", self.get_root(),
                                            Gtk.FileChooserAction.OPEN, "Choose", "Cancel")
        def response(dialog, code):
            if code == Gtk.ResponseType.ACCEPT:
                selected = dialog.get_file()
                if selected and selected.get_path():
                    self.entries["identity_file"].set_text(selected.get_path())
            dialog.destroy()
        chooser.connect("response", response)
        chooser.show()

    def save(self, *_):
        try:
            values = {key: entry.get_text().strip() for key, entry in self.entries.items()}
            values["port"] = int(values["port"]) if values["port"] else None
            connection = new_connection(**values, sudo=self.sudo.get_active(),
                                        connection_id=self.connection.id if self.connection else None)
        except (ConfigurationError, ValueError) as error:
            self.error.set_text(str(error))
            return
        self.save_button.set_sensitive(False)
        self.error.set_text("Saving connection…")
        self.set_can_close(False)

        def worker():
            try:
                matches = resolve_match_hosts(connection)
                if self.connection and connection.host == self.connection.host:
                    matches = list(dict.fromkeys([*matches, *self.connection.match_hosts]))
                self.store.save(replace(connection, match_hosts=matches),
                                create_only=self.connection is None, expected=self.connection)
                error = None
            except (ConfigurationError, OSError) as exc:
                error = str(exc)
            GLib.idle_add(self.saved, connection.id, error)
        threading.Thread(target=worker, daemon=True).start()

    def saved(self, connection_id, error):
        self.set_can_close(True)
        self.save_button.set_sensitive(True)
        if error:
            self.error.set_text(error)
        else:
            self.changed(connection_id)
            self.close()
        return GLib.SOURCE_REMOVE


class ConnectionsDialog(Adw.Dialog):
    def __init__(self, store, changed):
        super().__init__(title="Docker connections", content_width=540)
        self.store, self.changed = store, changed
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add SSH connection")
        add.connect("clicked", lambda *_: ConnectionEditor(store, self.edited).present(self))
        header.pack_start(add)
        toolbar.add_top_bar(header)
        self.group = Adw.PreferencesGroup(margin_top=20, margin_bottom=20,
                                          margin_start=20, margin_end=20)
        self.rows = []
        scroll = Gtk.ScrolledWindow(min_content_height=180, max_content_height=420,
                                    propagate_natural_height=True)
        scroll.set_child(self.group)
        toolbar.set_content(scroll)
        self.set_child(toolbar)
        self.render()

    def render(self):
        for row in self.rows:
            self.group.remove(row)
        self.rows.clear()
        try:
            connections = [c for c in self.store.load().values() if not c.local]
        except ConfigurationError as error:
            connections = []
            self.group.set_description(str(error))
        else:
            self.group.set_description("Add a hostname, IP address or existing SSH alias.")
        for connection in connections:
            row = Adw.ActionRow(title=connection.label, subtitle=connection.description)
            row.set_use_markup(False)
            edit = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit connection", valign=Gtk.Align.CENTER)
            edit.connect("clicked", lambda _, c=connection: ConnectionEditor(self.store, self.edited, c).present(self))
            row.add_suffix(edit)
            remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove connection", valign=Gtk.Align.CENTER)
            remove.connect("clicked", self.remove, connection.id)
            row.add_suffix(remove)
            self.group.add(row)
            self.rows.append(row)
        if not connections:
            row = Adw.ActionRow(title="No remote connections configured")
            self.group.add(row)
            self.rows.append(row)

    def edited(self, connection_id):
        self.render()
        self.changed(connection_id)

    def remove(self, _button, connection_id):
        try:
            self.store.remove(connection_id)
        except (ConfigurationError, OSError) as error:
            self.group.set_description(str(error))
        else:
            self.render()
            self.changed("local")
