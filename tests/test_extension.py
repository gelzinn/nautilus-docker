from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from nautilus_docker.extension import DockerMenuProvider
except (ImportError, ValueError):
    DockerMenuProvider = None
from nautilus_docker.connections import Connections, new_connection


@unittest.skipIf(DockerMenuProvider is None, 'nautilus-python is not installed')
class ExtensionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Connections(Path(self.temp.name) / 'connections.json')
        self.store.save(new_connection('Demo', 'server.example', connection_id='demo'))
        config = patch('nautilus_docker.extension.Connections', return_value=self.store)
        config.start()
        self.addCleanup(config.stop)
        self.provider = DockerMenuProvider()

    @patch('nautilus_docker.extension.launch')
    def test_remote_context_has_four_resources_for_only_that_server(self, launch):
        root = self.provider.menus(['sftp://server.example/home'])[0]
        self.assertEqual(root.props.label, 'Docker · Demo')
        items = root.props.menu.get_items()
        self.assertEqual([item.props.label for item in items],
                         ['Containers', 'Images', 'Volumes', 'Networks', 'Connections…'])
        items[2].emit('activate')
        launch.assert_called_once_with('demo', 'volumes')

    @patch('nautilus_docker.extension.launch')
    def test_unknown_server_offers_configuration(self, launch):
        item = self.provider.menus(['sftp://unknown.example/'])[0]
        self.assertEqual(item.props.label, 'Configure Docker connection…')
        item.emit('activate')
        launch.assert_called_once_with(manage=True)

    def test_menu_reads_new_connections_without_nautilus_restart(self):
        self.store.save(new_connection('Other', 'other.example', connection_id='other'))
        root = self.provider.menus(['sftp://other.example/'])[0]
        self.assertEqual(root.props.label, 'Docker · Other')

    @patch('nautilus_docker.extension.launch')
    def test_local_and_global_context(self, launch):
        local = self.provider.menus(['file:///tmp'])[0]
        self.assertEqual(local.props.label, 'Docker · Local')
        global_menu = self.provider.menus(['network:///'])[0]
        global_menu.props.menu.get_items()[0].emit('activate')
        launch.assert_called_once_with('all', 'containers')
