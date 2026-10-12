"""Native resource browser with independent host and resource selection."""

import threading
from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .connections import ConfigurationError, LOCAL
from .connection_ui import ConnectionsDialog
from .docker import QueryError, list_items
from .local_session import LocalDockerSession
from .resources import SECTIONS
from .widgets import DockerItem, Details, table, text


class DockerWindow(Adw.ApplicationWindow):
    def __init__(self, app, connections, selected="local", section="containers"):
        super().__init__(application=app, title="Docker", default_width=1280, default_height=760)
        self.add_css_class("nautilus-window")
        self.add_css_class("docker-viewer")
        self.connections_store = connections
        self.connections = self.load_connections()
        self.selected_host = selected if selected in self.connections or selected == "all" else "local"
        self.section = section
        self.results, self.generations = {}, {}
        self.local_session = LocalDockerSession()
        self.closed = False
        self.connect("close-request", self.on_close)

        split = Adw.OverlaySplitView(min_sidebar_width=240, max_sidebar_width=240,
                                     sidebar_width_unit=Adw.LengthUnit.PX,
                                     enable_hide_gesture=False, enable_show_gesture=False)
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar(show_end_title_buttons=False)
        sidebar_header.set_title_widget(Adw.WindowTitle(title="Docker"))
        sidebar_toolbar.add_top_bar(sidebar_header)
        navigation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.sections_list = self.new_sidebar()
        self.section_rows = {}
        for key, spec in SECTIONS.items():
            row, _ = self.navigation_row(spec["label"], spec["icon"])
            self.sections_list.append(row)
            self.section_rows[key] = row
        self.sections_list.select_row(self.section_rows[section])
        self.sections_list.connect("row-selected", self.on_section_selected)
        navigation.append(self.sections_list)
        navigation.append(Gtk.Separator(margin_top=6, margin_bottom=6))
        hosts_title = text("Hosts", dim=True)
        hosts_title.set_margin_start(12)
        hosts_title.set_margin_top(6)
        hosts_title.set_margin_bottom(6)
        navigation.append(hosts_title)
        self.sidebar = self.new_sidebar()
        self.rebuild_hosts()
        self.sidebar.connect("row-selected", self.on_host_selected)
        navigation.append(self.sidebar)
        manage = Gtk.Button(label="Connections…", margin_start=6, margin_end=6, margin_bottom=6)
        manage.add_css_class("flat")
        manage.connect("clicked", lambda *_: self.manage_connections())
        navigation.append(manage)
        sidebar_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        sidebar_scroll.set_child(navigation)
        sidebar_toolbar.set_content(sidebar_scroll)
        split.set_sidebar(sidebar_toolbar)

        main_toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False)
        self.path = text()
        self.path.add_css_class("docker-pathbar")
        self.path.set_size_request(300, -1)
        header.set_title_widget(self.path)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        self.refresh_button.connect("clicked", lambda *_: self.refresh())
        header.pack_start(self.refresh_button)
        self.search_button = Gtk.ToggleButton(icon_name="edit-find-symbolic", tooltip_text="Search")
        header.pack_end(self.search_button)
        self.info_button = Gtk.ToggleButton(icon_name="dialog-information-symbolic", tooltip_text="Show details")
        header.pack_end(self.info_button)
        main_toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        search_bar = Gtk.SearchBar()
        self.search = Gtk.SearchEntry(placeholder_text="Search this section", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.render())
        search_bar.set_child(self.search)
        search_bar.connect_entry(self.search)
        self.search_button.bind_property("active", search_bar, "search-mode-enabled",
                                         GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        content.append(search_bar)
        self.notices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.notices.add_css_class("docker-notice")
        content.append(self.notices)
        self.items_store = Gio.ListStore.new(DockerItem)
        self.sorted_items = Gtk.SortListModel(model=self.items_store)
        self.selection = Gtk.SingleSelection(model=self.sorted_items, autoselect=False, can_unselect=True)
        self.selection.connect("notify::selected-item", self.on_item_selected)
        self.scroll = Gtk.ScrolledWindow(vexpand=True)
        self.details_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rebuild_table()
        self.detail_split = Adw.OverlaySplitView(sidebar_position=Gtk.PackType.END,
                                                min_sidebar_width=280, max_sidebar_width=320,
                                                sidebar_width_unit=Adw.LengthUnit.PX,
                                                show_sidebar=False)
        self.detail_split.set_content(self.scroll)
        self.detail_split.set_sidebar(self.details_scroll)
        self.info_button.bind_property("active", self.detail_split, "show-sidebar",
                                       GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        content.append(self.detail_split)
        self.status = text()
        self.status.add_css_class("docker-status")
        content.append(self.status)
        main_toolbar.set_content(content)
        split.set_content(main_toolbar)
        self.set_content(split)
        self.refresh()

    def load_connections(self):
        try:
            connections = self.connections_store.load()
            self.config_error = None
            return connections
        except ConfigurationError as error:
            self.config_error = str(error)
            return {"local": LOCAL}

    @staticmethod
    def new_sidebar():
        # The navigation-sidebar theme already supplies list padding and row margins.
        sidebar = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        sidebar.add_css_class("navigation-sidebar")
        return sidebar

    @staticmethod
    def navigation_row(title, icon):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(spacing=8)
        box.append(Gtk.Image.new_from_icon_name(icon))
        label = text(title)
        label.set_hexpand(True)
        box.append(label)
        count = text("", dim=True)
        box.append(count)
        row.set_child(box)
        return row, count

    def rebuild_hosts(self):
        self.rebuilding_hosts = True
        while self.sidebar.get_first_child():
            self.sidebar.remove(self.sidebar.get_first_child())
        self.sidebar_rows, self.sidebar_counts = {}, {}
        entries = [("all", "All hosts", "view-list-symbolic"),
                   *[(c.id, c.label, "computer-symbolic" if c.local else "network-server-symbolic")
                     for c in self.connections.values()]]
        for key, title, icon in entries:
            row, count = self.navigation_row(title, icon)
            self.sidebar.append(row)
            self.sidebar_rows[key], self.sidebar_counts[key] = row, count
        self.sidebar.select_row(self.sidebar_rows[self.selected_host])
        self.rebuilding_hosts = False

    def rebuild_table(self):
        self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self.items_store.remove_all()
        self.table = table(self.selection, self.section)
        self.sorted_items.set_sorter(self.table.get_sorter())
        self.table.connect("activate", self.on_item_activated)
        self.scroll.set_child(self.table)
        self.details = Details(self.section)
        self.details_scroll.set_child(self.details)

    def on_close(self, *_):
        self.closed = True
        self.local_session.close()
        return False

    def hosts_in_view(self):
        return list(self.connections) if self.selected_host == "all" else [self.selected_host]

    def on_host_selected(self, _list, row):
        if row is None or self.rebuilding_hosts:
            return
        self.selected_host = next(key for key, candidate in self.sidebar_rows.items() if candidate == row)
        self.ensure_queries()

    def on_section_selected(self, _list, row):
        if row is None:
            return
        section = next(key for key, candidate in self.section_rows.items() if candidate == row)
        if section != self.section:
            self.section = section
            self.rebuild_table()
            self.search.set_text("")
            self.ensure_queries()

    def select_view(self, host, section):
        if host in self.sidebar_rows:
            self.sidebar.select_row(self.sidebar_rows[host])
        self.sections_list.select_row(self.section_rows[section])
        self.ensure_queries()

    def ensure_queries(self):
        for host in self.hosts_in_view():
            if (host, self.section) not in self.results:
                self.query(host)
        self.render()

    def refresh(self):
        connections = self.load_connections()
        if connections != self.connections:
            self.connections = connections
            if self.selected_host != "all" and self.selected_host not in connections:
                self.selected_host = "local"
            self.results.clear()
            self.rebuild_hosts()
        for host in self.hosts_in_view():
            self.query(host)
        self.render()

    def query(self, host, authorize=False):
        section = self.section
        key = (host, section)
        generation = self.generations.get(key, 0) + 1
        self.generations[key] = generation
        self.results[key] = {"items": self.results.get(key, {}).get("items", []),
                             "error": None, "loading": True}
        connection = self.connections[host]

        def worker():
            try:
                items, error = list_items(connection, section, authorize,
                                           local_session=self.local_session if connection.local else None), None
            except QueryError as exc:
                items, error = [], exc
            GLib.idle_add(self.show_result, connection, section, generation, items, error, authorize)
        threading.Thread(target=worker, daemon=True).start()

    def show_result(self, connection, section, generation, items, error, authorized):
        key = (connection.id, section)
        if self.closed or generation != self.generations.get(key) or self.connections.get(connection.id) != connection:
            return GLib.SOURCE_REMOVE
        self.results[key] = {"items": items, "error": error, "loading": False}
        self.render()
        return GLib.SOURCE_REMOVE

    def render(self):
        current = self.selection.get_selected_item()
        selected_id = current.identity if current else None
        search = self.search.get_text().casefold().strip()
        items = [DockerItem(host, self.connections[host].label, data, self.section)
                 for host in self.hosts_in_view()
                 for data in self.results.get((host, self.section), {}).get("items", [])]
        visible = [item for item in items if search in item.search_text]
        self.table.get_columns().get_item(1).set_visible(self.selected_host == "all")
        self.items_store.splice(0, self.items_store.get_n_items(), visible)
        if selected_id:
            for index in range(self.sorted_items.get_n_items()):
                item = self.sorted_items.get_item(index)
                if item.identity == selected_id:
                    self.selection.set_selected(index)
                    break
        host_label = "All hosts" if self.selected_host == "all" else self.connections[self.selected_host].label
        spec = SECTIONS[self.section]
        self.path.set_text(f"Docker / {host_label} / {spec['label']}")
        self.set_title(f"Docker · {host_label} · {spec['label']}")
        total = 0
        for host, label in self.sidebar_counts.items():
            if host == "all":
                continue
            result = self.results.get((host, self.section))
            value = "" if result is None else ("…" if result["loading"] else ("!" if result["error"] else str(len(result["items"]))))
            label.set_text(value)
            if result and not result["error"]:
                total += len(result["items"])
        all_loaded = all((host, self.section) in self.results and not self.results[(host, self.section)]["error"]
                         and not self.results[(host, self.section)]["loading"] for host in self.connections)
        self.sidebar_counts["all"].set_text(str(total) if all_loaded else "")
        loading = any(self.results.get((host, self.section), {}).get("loading") for host in self.hosts_in_view())
        self.refresh_button.set_sensitive(not loading)
        count_label = spec["singular"] if len(visible) == 1 else spec["plural"]
        self.status.set_text(f"{len(visible)} {count_label}" + (f" of {len(items)}" if search else "") + (" · Refreshing…" if loading else ""))
        self.render_notices()

    def render_notices(self):
        while self.notices.get_first_child():
            self.notices.remove(self.notices.get_first_child())
        if self.config_error:
            self.notices.append(text(self.config_error, wrap=True))
        for host in self.hosts_in_view():
            error = self.results.get((host, self.section), {}).get("error")
            if not error:
                continue
            row = Gtk.Box(spacing=12)
            permission = host == "local" and error.kind == "permission"
            inactive = host == "local" and error.kind == "inactive"
            message = text("Local: authorize access to query Docker." if permission else (
                "Local: Docker is not running." if inactive else f"{self.connections[host].label}: {str(error).splitlines()[0]}"))
            message.set_hexpand(True)
            message.set_tooltip_text(str(error))
            row.append(message)
            button = Gtk.Button(label="Authorize" if permission else ("Check again" if inactive else "Retry"))
            button.connect("clicked", self.retry_host, host, permission)
            row.append(button)
            self.notices.append(row)
        self.notices.set_visible(self.notices.get_first_child() is not None)

    def retry_host(self, _button, host, authorize):
        self.query(host, authorize)
        self.render()

    def on_item_selected(self, *_):
        if hasattr(self, "details"):
            self.details.show_item(self.selection.get_selected_item())

    def on_item_activated(self, _view, position):
        self.selection.set_selected(position)
        self.info_button.set_active(True)

    def manage_connections(self):
        ConnectionsDialog(self.connections_store, self.connections_changed).present(self)

    def connections_changed(self, selected):
        self.connections = self.load_connections()
        self.selected_host = selected if selected in self.connections else "local"
        self.results.clear()
        self.generations = {key: value + 1 for key, value in self.generations.items()}
        self.rebuild_hosts()
        self.refresh()
