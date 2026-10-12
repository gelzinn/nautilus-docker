"""Exercise the GTK browser without mapping a window or contacting Docker."""
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk
from nautilus_docker.connections import Connections, new_connection
from nautilus_docker.connection_ui import ConnectionEditor, ConnectionsDialog
from nautilus_docker.resources import SECTIONS, stylesheet
from nautilus_docker.window import DockerWindow


def sample(connection, section, authorize=False, local_session=None):
    result = {key: '' for key in SECTIONS[section]['fields']}
    result[SECTIONS[section]['name_key']] = 'demo-' + section
    result['ID'] = section + '-id'
    return [result]


def wait_loaded(window):
    deadline = time.monotonic() + 3
    while any(r['loading'] for r in window.results.values()):
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        if time.monotonic() > deadline:
            raise AssertionError('GTK query result timed out')
        time.sleep(.01)


with tempfile.TemporaryDirectory() as temp, patch('nautilus_docker.window.list_items', side_effect=sample):
    settings = Connections(Path(temp) / 'connections.json')
    settings.save(new_connection('Demo', 'server.example', connection_id='demo'))
    app = Adw.Application(application_id='org.nautilusdocker.Test')
    app.register(None)
    provider = Gtk.CssProvider()
    errors = []
    provider.connect('parsing-error', lambda *args: errors.append(args[-1]))
    provider.load_from_string(stylesheet())
    assert not errors, errors
    window = DockerWindow(app, settings, 'demo')
    # GTK's destruction path expects a native surface even for an unmapped window.
    window.realize()
    wait_loaded(window)
    for section in SECTIONS:
        window.select_view('demo', section)
        wait_loaded(window)
        assert window.items_store.get_n_items() == 1
        item = window.items_store.get_item(0)
        assert item.section == section
        window.selection.set_selected(0)
        assert window.details.name.get_text() == 'demo-' + section
        window.search.set_text('nonexistent')
        window.render()
        assert window.items_store.get_n_items() == 0
        window.search.set_text('')
        window.render()
    assert len(window.results) == 4
    window.select_view('demo', 'images')
    image_rows = []
    for name, size, created in [('zebra', '1.2GB', '2024-01-02'),
                                 ('Alpha', '950MB', '2024-01-03'),
                                 ('beta', '20MB', '2024-01-01')]:
        row = {key: '' for key in SECTIONS['images']['fields']}
        row.update(Repository=name, ID=name, Size=size, CreatedAt=created + ' 12:00:00 +0000 UTC')
        image_rows.append(row)
    window.results[('demo', 'images')]['items'] = image_rows
    window.render()
    def order():
        return [window.sorted_items.get_item(i).data['Repository'] for i in range(3)]
    assert order() == ['Alpha', 'beta', 'zebra']
    size_column = window.table.get_columns().get_item(3)
    window.table.sort_by_column(size_column, Gtk.SortType.ASCENDING)
    assert order() == ['beta', 'Alpha', 'zebra']
    window.table.sort_by_column(size_column, Gtk.SortType.DESCENDING)
    assert order() == ['zebra', 'Alpha', 'beta']
    window.selection.set_selected(1)
    window.render()
    assert window.selection.get_selected_item().data['Repository'] == 'Alpha'
    window.table.sort_by_column(window.table.get_columns().get_item(4), Gtk.SortType.ASCENDING)
    assert order() == ['beta', 'zebra', 'Alpha']
    for index in range(window.table.get_columns().get_n_items()):
        column = window.table.get_columns().get_item(index)
        assert column.get_sorter() is not None
        window.table.sort_by_column(column, Gtk.SortType.ASCENDING)
        window.table.sort_by_column(column, Gtk.SortType.DESCENDING)
    settings.save(new_connection('Other', 'other.example', connection_id='other'))
    window.connections_changed('other')
    wait_loaded(window)
    assert window.selected_host == 'other'
    assert 'other' in window.sidebar_rows
    editor = ConnectionEditor(settings, lambda *_: None)
    dialog = ConnectionsDialog(settings, lambda *_: None)
    assert len(dialog.rows) == 2
    window.select_view('all', 'images')
    wait_loaded(window)
    assert window.items_store.get_n_items() == 3
    # The CLI writes this same store outside the GUI; refresh must reload it.
    settings.save(new_connection('From terminal', 'terminal.example', connection_id='terminal'))
    window.refresh()
    wait_loaded(window)
    assert window.selected_host == 'all'
    assert 'terminal' in window.sidebar_rows
    assert window.items_store.get_n_items() == 4
    window.select_view('terminal', 'images')
    wait_loaded(window)
    settings.save(new_connection('Renamed in terminal', 'terminal.example', connection_id='terminal'))
    window.refresh()
    wait_loaded(window)
    assert window.connections['terminal'].label == 'Renamed in terminal'
    settings.remove('terminal')
    window.refresh()
    wait_loaded(window)
    assert window.selected_host == 'local'
    assert 'terminal' not in window.sidebar_rows
    assert not window.get_visible()
    window.on_close()
    window.set_focus(None)
    window.set_content(None)
    window.destroy()
    app.quit()
print('GTK integration passed: sections, search, details, connections, external configuration changes, header sorting and selection; no window displayed')
