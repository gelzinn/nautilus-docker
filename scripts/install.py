#!/usr/bin/env python3
"""Install user-scoped launchers pointing at this checkout. No root required."""

from datetime import datetime
import os
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
BACKUP = STATE / "nautilus-docker/install-backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def link(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() and destination.resolve() == source:
        return
    if destination.exists() or destination.is_symlink():
        BACKUP.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.move(str(destination), str(BACKUP / destination.name))
        print(f"Previous file saved to {BACKUP / destination.name}")
    destination.symlink_to(source)
    print(f"Installed {destination}")


def main():
    link(ROOT / "bin/nautilus-docker", Path.home() / ".local/bin/nautilus-docker")
    link(ROOT / "integration/nautilus_docker_extension.py",
         DATA / "nautilus-python/extensions/nautilus_docker_extension.py")
    desktop = DATA / "applications/nautilus-docker.desktop"
    desktop.parent.mkdir(parents=True, exist_ok=True)
    # Quote the launcher path for desktop sessions that omit ~/.local/bin from PATH.
    launcher = str(Path.home() / ".local/bin/nautilus-docker")
    quoted = launcher.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Nautilus Docker
Comment=Browse local and SSH Docker resources
Exec="{quoted}"
Icon=applications-engineering-symbolic
Terminal=false
Categories=System;Utility;
StartupNotify=true
''')
    print("Restart GNOME Files to load the extension: nautilus -q")
    print("Your connections are stored separately under the XDG configuration directory.")


if __name__ == "__main__":
    main()
