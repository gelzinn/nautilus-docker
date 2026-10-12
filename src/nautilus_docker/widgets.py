"""Native GTK list and detail widgets for the Docker window."""

from gi.repository import GObject, Gtk, Pango
from .resources import SECTIONS
from .sorting import sort_value

STATES = {"running": "Running", "exited": "Stopped", "paused": "Paused",
          "restarting": "Restarting", "created": "Created", "removing": "Removing", "dead": "Error"}


class DockerItem(GObject.GObject):
    def __init__(self, host, host_label, data, section="containers"):
        super().__init__()
        self.host, self.host_label, self.data = host, host_label, data
        self.section = section
        self.identity = (host, section, data.get("ID", ""), data.get("Name", data.get("Names", "")), data.get("Repository", ""), data.get("Tag", ""))
        self.search_text = " ".join((host_label, *data.values())).casefold()


def text(value="", dim=False, wrap=False):
    widget = Gtk.Label(label=value, xalign=0, selectable=False, wrap=wrap)
    if wrap:
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if not wrap:
        widget.set_ellipsize(Pango.EllipsizeMode.END)
    if dim:
        widget.add_css_class("dim-label")
    return widget


def cell_factory(key, section):
    spec = SECTIONS[section]
    is_name = key == spec["name_key"]
    factory = Gtk.SignalListItemFactory()

    def setup(_factory, item):
        if is_name:
            box = Gtk.Box(spacing=10)
            box.append(Gtk.Image.new_from_icon_name(spec["icon"]))
            name = text()
            name.set_hexpand(True)
            box.append(name)
            item.set_child(box)
        else:
            item.set_child(text())

    def bind(_factory, item):
        container = item.get_item()
        widget = item.get_child()
        label = widget.get_last_child() if is_name else widget
        value = container.host_label if key == "host" else container.data.get(key, "")
        for style in ("success", "warning", "dim-label"):
            label.remove_css_class(style)
        if key == "State":
            value = STATES.get(value, value)
            label.add_css_class("success" if container.data[key] == "running" else "dim-label")
            label.set_tooltip_text(container.data["Status"])
        else:
            label.set_tooltip_text(value)
            if key in ("Image", "Ports", "host"):
                label.add_css_class("dim-label")
        if key == "ID" and value:
            value = value.removeprefix("sha256:")[:12]
        label.set_text(value or "—")

    factory.connect("setup", setup)
    factory.connect("bind", bind)
    return factory


def table(selection, section="containers"):
    view = Gtk.ColumnView(model=selection, show_column_separators=False,
                          show_row_separators=False, single_click_activate=False)
    view.add_css_class("container-table")
    for title, key, width, expand in SECTIONS[section]["columns"]:
        column = Gtk.ColumnViewColumn.new(title, cell_factory(key, section))
        column.set_resizable(True)
        column.set_expand(expand)
        column.set_fixed_width(width)
        def compare(left, right, _data=None, field=key):
            a, b = sort_value(left, field, STATES), sort_value(right, field, STATES)
            return Gtk.Ordering.SMALLER if a < b else (Gtk.Ordering.LARGER if a > b else Gtk.Ordering.EQUAL)
        column.set_sorter(Gtk.CustomSorter.new(compare))
        view.append_column(column)
    view.sort_by_column(view.get_columns().get_item(0), Gtk.SortType.ASCENDING)
    return view


class Details(Gtk.Box):
    def __init__(self, section="containers"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                         margin_start=20, margin_end=20, margin_top=20, margin_bottom=20)
        self.section = section
        self.name = text("Select an item", wrap=True)
        self.name.add_css_class("heading")
        self.append(self.name)
        self.fields = {}
        for title, key in [("Host", "host"), *SECTIONS[section]["details"]]:
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            group.append(text(title, dim=True))
            value = text("—", wrap=True)
            value.set_selectable(True)
            group.append(value)
            self.append(group)
            self.fields[key] = value

    def show_item(self, container):
        self.name.set_text(container.data[SECTIONS[self.section]["name_key"]] if container else "Select an item")
        for key, widget in self.fields.items():
            value = container.host_label if container and key == "host" else (
                container.data.get(key, "") if container else "")
            widget.set_text(value or "—")
