#!/usr/bin/env python3
"""Install optional read-only local access for one user on systemd Linux."""

import argparse
from datetime import datetime
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LIB = Path('/usr/local/lib/nautilus-docker')
UNITS = Path('/etc/systemd/system')


def unit_contents(account):
    uid, name = account.pw_uid, account.pw_name
    if uid == 0 or not re.fullmatch(r'[a-zA-Z0-9_][a-zA-Z0-9_.-]*', name):
        raise ValueError('Choose a valid non-root account.')
    base = f'nautilus-docker-readonly-{uid}'
    python = shutil.which('python3', path=os.defpath)
    if not python:
        raise RuntimeError('System Python 3 is required.')
    socket = f'''[Unit]
Description=Nautilus Docker read-only access for {name}

[Socket]
ListenStream=/run/nautilus-docker/{uid}.sock
SocketUser={name}
SocketMode=0600
DirectoryMode=0755
Accept=yes
MaxConnections=4
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
'''
    service = f'''[Unit]
Description=Nautilus Docker read-only query session for {name}
CollectMode=inactive-or-failed

[Service]
Type=exec
ExecStart={python} -I {LIB}/local_helper.py --socket unix:///var/run/docker.sock --peer-uid {uid}
StandardInput=socket
StandardOutput=inherit
StandardError=journal
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX
LockPersonality=yes
UMask=0077
MemoryMax=256M
TasksMax=64
'''
    return {f'{base}.socket': socket, f'{base}@.service': service}


def install(account):
    units = unit_contents(account)
    LIB.mkdir(parents=True, exist_ok=True, mode=0o755)
    if LIB.is_symlink():
        raise RuntimeError('The helper directory must not be a symlink.')
    os.chown(LIB, 0, 0)
    LIB.chmod(0o755)
    destinations = {LIB / 'local_helper.py': (ROOT / 'src/nautilus_docker/local_helper.py').read_text(),
                    **{UNITS / name: content for name, content in units.items()}}
    backup = Path('/var/lib/nautilus-docker/install-backups') / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    for destination, content in destinations.items():
        if destination.exists() and destination.read_text() != content:
            backup.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(destination, backup / destination.name)
        with tempfile.NamedTemporaryFile('w', dir=destination.parent, delete=False) as stream:
            stream.write(content)
            temporary = Path(stream.name)
        temporary.chmod(0o644)
        os.chown(temporary, 0, 0)
        temporary.replace(destination)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    name = f'nautilus-docker-readonly-{account.pw_uid}.socket'
    subprocess.run(['systemctl', 'enable', '--now', name], check=True)
    print(f'Read-only local access enabled for {account.pw_name}. No Docker resources were changed.')


def remove(account):
    base = f'nautilus-docker-readonly-{account.pw_uid}'
    subprocess.run(['systemctl', 'disable', '--now', base + '.socket'], check=True)
    subprocess.run(['systemctl', 'stop', base + '@*.service'], check=True)
    for name in (base + '.socket', base + '@.service'):
        (UNITS / name).unlink(missing_ok=True)
    if not list(UNITS.glob('nautilus-docker-readonly-*.socket')):
        (LIB / 'local_helper.py').unlink(missing_ok=True)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    print(f'Read-only local access removed for {account.pw_name}.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--user', required=True, help='Non-root account allowed to browse local resources')
    parser.add_argument('--remove', action='store_true', help='Remove this account\'s read-only access')
    parser.add_argument('--preview', action='store_true', help='Print units without installing')
    args = parser.parse_args()
    account = pwd.getpwnam(args.user)
    units = unit_contents(account)
    if args.preview:
        for name, content in units.items():
            print(f'# {name}\n{content}')
        return
    if os.geteuid() != 0:
        parser.error('Run installation or removal with sudo/pkexec.')
    (remove if args.remove else install)(account)


if __name__ == '__main__':
    main()
