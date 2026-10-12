#!/usr/bin/env python3
"""Check release contents and exercise the installer from an extracted archive."""

from email.parser import BytesParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from nautilus_docker import __version__


def main():
    source = ROOT / 'dist' / f'nautilus_docker-{__version__}.tar.gz'
    wheel = ROOT / 'dist' / f'nautilus_docker-{__version__}-py3-none-any.whl'
    required = {
        'README.md', 'LICENSE', 'CHANGELOG.md', 'pyproject.toml',
        'bin/nautilus-docker', 'integration/nautilus_docker_extension.py',
        'scripts/install.py', 'scripts/install_local_access.py',
        'src/nautilus_docker/local_helper.py',
        'src/nautilus_docker/resources/style.css',
        'src/nautilus_docker/resources/sections.json',
    }
    with tempfile.TemporaryDirectory() as directory, tarfile.open(source) as archive:
        prefix = f'nautilus_docker-{__version__}/'
        contents = {name.removeprefix(prefix) for name in archive.getnames()}
        missing = required - contents
        if missing:
            raise RuntimeError(f'Source archive is missing: {sorted(missing)}')
        archive.extractall(directory, filter='data')
        extracted = Path(directory) / prefix
        result = subprocess.check_output([sys.executable, str(extracted / 'bin/nautilus-docker'), '--version'], text=True)
        if result.strip() != __version__:
            raise RuntimeError('Source archive version does not match the package.')
        # Exercise symlinks and the desktop file in an isolated user directory.
        install_home = Path(directory) / 'home'
        environment = dict(os.environ, HOME=str(install_home),
                           XDG_CONFIG_HOME=str(install_home / '.config'),
                           XDG_DATA_HOME=str(install_home / '.local/share'),
                           XDG_STATE_HOME=str(install_home / '.local/state'))
        subprocess.run([sys.executable, str(extracted / 'scripts/install.py')],
                       env=environment, check=True, capture_output=True, text=True)
        launcher = install_home / '.local/bin/nautilus-docker'
        extension = install_home / '.local/share/nautilus-python/extensions/nautilus_docker_extension.py'
        desktop = install_home / '.local/share/applications/nautilus-docker.desktop'
        if not launcher.is_file() or not extension.is_file() or not desktop.is_file():
            raise RuntimeError('The source installer did not create all integration files.')
        subprocess.run([str(launcher), '--help'], check=True, capture_output=True)
        connections = json.loads(subprocess.check_output(
            [str(launcher), 'connections', 'list', '--json'], env=environment, text=True))
        if len(connections) != 1 or connections[0]['id'] != 'local':
            raise RuntimeError('The packaged connection CLI did not load the isolated default configuration.')

    with zipfile.ZipFile(wheel) as archive:
        for resource in ('style.css', 'sections.json'):
            if f'nautilus_docker/resources/{resource}' not in archive.namelist():
                raise RuntimeError(f'Wheel is missing {resource}.')
        metadata_path = f'nautilus_docker-{__version__}.dist-info'
        metadata = BytesParser().parsebytes(archive.read(f'{metadata_path}/METADATA'))
        if metadata['Version'] != __version__ or metadata['License-Expression'] != 'MIT':
            raise RuntimeError('Wheel version or license metadata is incorrect.')
        entry_points = archive.read(f'{metadata_path}/entry_points.txt').decode()
        if 'nautilus-docker = nautilus_docker.app:main' not in entry_points:
            raise RuntimeError('Wheel is missing the application entry point.')
    print(f'Release packages validated: {source.name}, {wheel.name}; extracted installer passed')


if __name__ == '__main__':
    main()
