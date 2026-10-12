# Nautilus Docker

Browse Docker containers, images, volumes and networks from GNOME Files (Nautilus), locally or over SSH.

Nautilus Docker adds a **Docker** context menu to Files and opens a native GTK window with the connection that matches your current folder. You can also launch it from the application menu or the terminal.

## Features

| Feature | Description |
| --- | --- |
| **Four resource views** | Browse containers, images, volumes and networks, including stopped containers |
| **Local and SSH hosts** | Switch between individual connections or view all configured hosts |
| **Connection CLI** | List, add, edit and remove SSH connections from the terminal |
| **Nautilus integration** | Select the host from a local folder or a matching SSH location |
| **Native interface** | GTK and libadwaita styling, compact lists and a familiar sidebar |
| **Search and sorting** | Filter results and sort columns, including numeric sizes and creation dates |
| **Resource details** | Show an optional details pane for the selected item |
| **Read-only access** | List resources without starting, stopping, deleting or pruning them |

The interface is in English. Queries run outside the Nautilus process, keeping Files responsive.

## Requirements

- Linux with Python 3.11 or later and PyGObject available to the system Python interpreter.
- GTK 4.16 or later and libadwaita 1.6 or later.
- GNOME Files with the `nautilus-python` extension loader.
- The Docker CLI and an already-running Docker daemon for local browsing.
- OpenSSH for remote connections, and PolicyKit (`pkexec`) for local administrator authorization when needed.
- systemd only if you choose the optional automatic local access helper.

On Arch Linux, install the dependencies with:

```sh
sudo pacman -S python-gobject gtk4 libadwaita nautilus nautilus-python docker openssh polkit
```

Installing Nautilus Docker does not start Docker or configure remote servers. GTK 4.16 is required by the interface's [CSS variables](https://docs.gtk.org/gtk4/css-properties.html#custom-properties).

## Installation

### From a release

Download the `nautilus_docker-X.Y.Z.tar.gz` asset from [GitHub Releases](https://github.com/gelzinn/nautilus-docker/releases). Extract it into a directory you will keep, then run its installer. For example, for version `0.1.0`:

```sh
mkdir -p ~/.local/share/nautilus-docker
tar -xzf nautilus_docker-0.1.0.tar.gz -C ~/.local/share/nautilus-docker --strip-components=1
python3 ~/.local/share/nautilus-docker/scripts/install.py
nautilus -q
```

Reopen Files and right-click a folder to choose **Docker → Containers / Images / Volumes / Networks**. The application menu entry and `~/.local/bin/nautilus-docker` open the browser directly.

The source archive includes the Nautilus extension and both installers. The wheel is provided for Python packaging; installing a wheel alone does not register the Files extension or the desktop launcher.

### From a Git checkout

```sh
git clone https://github.com/gelzinn/nautilus-docker.git
cd nautilus-docker
python3 scripts/install.py
nautilus -q
```

The installer creates user-scoped symlinks to the extracted directory or checkout. Keep that directory in place; if you move it, rerun the installer. No root access is required for the application installation. Existing launcher and extension files are backed up under `$XDG_STATE_HOME/nautilus-docker/install-backups` (default: `~/.local/state`).

To update, extract a newer release into the same directory, or run `git pull --ff-only` in the checkout, then rerun `scripts/install.py` and restart Files. Close and reopen Nautilus Docker to load the new code. If you installed the optional system helper, rerun its administrator installer too.

## Using the browser

Choose a resource view in the sidebar, then select **Local**, an SSH connection or **All hosts**. Use the refresh button to query again, search to filter the current results, and the information button to show details. Click a column header to sort; click again to reverse the order.

Terminal shortcuts:

```sh
nautilus-docker --section images --host local
nautilus-docker --host all
nautilus-docker --connections
nautilus-docker --help
```

### Manage connections from the terminal

The CLI manages the same connection settings as the graphical interface. It works without GTK or a display and does not wrap Docker's command-line operations.

Add a server with a stable ID, a display name and optional SSH settings:

```sh
nautilus-docker connections add production deploy@server.example \
  --name "Production" --port 2222 --identity ~/.ssh/id_ed25519
nautilus-docker connections list
nautilus-docker connections show production
```

The ID is also used to open the browser, including the network view:

```sh
nautilus-docker --host production --section networks
```

You can use an existing SSH alias as the host. Username, port and key options are optional. Adding a connection reads OpenSSH's effective configuration with `ssh -G`; it does not connect to the server or query Docker. Adding an existing ID is rejected.

Edit only the fields you want to change:

```sh
nautilus-docker connections edit production --name "Production server" --sudo
nautilus-docker connections edit production --default-port --user "" --identity "" --no-sudo
```

An empty user or identity returns that setting to your SSH configuration; `--default-port` clears the saved port. Explicit key paths are saved as absolute paths so launching from Files uses the same key. `--sudo` enables the same non-interactive fallback as the graphical checkbox. Use `connections list --json` or `connections show production --json` for scripts.

To remove a saved remote connection:

```sh
nautilus-docker connections remove production
```

This removes the saved connection entry. The built-in Local connection cannot be edited or removed. Click Refresh in an open browser window, or open it again, to load changes made from the CLI. Concurrent changes are protected; if someone changes or removes a connection while you edit it, reload its settings and retry. Run `nautilus-docker connections --help` for command help. Successful commands return `0`, configuration or filesystem errors return `1`, and invalid command syntax returns `2`.

### SSH connections

Click **Connections…**, then **+**. Enter a display name and a hostname, IP address or existing SSH alias. Username, port and key file are optional; empty fields use your OpenSSH configuration and agent. The host field also accepts `user@host`.

Connections use SSH keys or an SSH agent. Password prompts are not supported. Test authentication with `ssh user@server` first. OpenSSH accepts and records new host keys; changed keys are rejected. Nautilus Docker stores neither passwords nor private key contents.

If the remote account needs administrator privileges to query Docker, enable **Use sudo when Docker requires it**. This uses `sudo -n` after a Docker socket permission error. Configure non-interactive sudo on that server separately.

When you save an SSH alias, the application records the effective hostname from `ssh -G` for Nautilus folder matching. Unknown SSH folders offer **Configure Docker connection…**. Local folders select Local; global locations select all configured hosts.

### Local access

Rootless Docker uses `$XDG_RUNTIME_DIR/docker.sock` when present; otherwise the application queries `/var/run/docker.sock`. With normal socket permissions, local browsing works immediately. If access is denied, click **Authorize** for graphical PolicyKit authorization.

One read-only helper serves all four views and refreshes while that window remains open. Closing the window ends the authorized session; opening another window may request authorization again. The application does not add your account to the Docker group or install PolicyKit rules.

For automatic read-only local access on systemd Linux, run this optional administrator installer from the extracted release or checkout:

```sh
sudo python3 scripts/install_local_access.py --user "$(id -un)"
```

It copies the helper into a root-owned directory and enables a user-specific Unix socket. Nautilus Docker detects it automatically. The kernel-reported client UID must match the configured user, and the helper accepts only four fixed list operations. This installation does not start the Docker daemon.

You can inspect the generated units without installing them:

```sh
python3 scripts/install_local_access.py --user "$(id -un)" --preview
```

To remove the optional helper for your account:

```sh
sudo python3 scripts/install_local_access.py --user "$(id -un)" --remove
```

## Configuration and privacy

Connections live in `$XDG_CONFIG_HOME/nautilus-docker/connections.json` (default: `~/.config/nautilus-docker/connections.json`) with mode `0600`. A fresh installation contains only Local. Usernames, host addresses, UIDs and credentials are not bundled into releases; user-specific systemd units are generated during installation.

Queries use Docker list output. The details pane does not inspect container environment variables or secrets. The local helper rejects arbitrary arguments and resource mutations, runs system Python in isolated mode, and exits when its client disconnects. Nautilus Docker does not read your login password from a keyring.

## Uninstall

Remove the user-scoped integration files, then restart Files:

```sh
rm ~/.local/bin/nautilus-docker
rm "${XDG_DATA_HOME:-$HOME/.local/share}/nautilus-python/extensions/nautilus_docker_extension.py"
rm "${XDG_DATA_HOME:-$HOME/.local/share}/applications/nautilus-docker.desktop"
nautilus -q
```

If enabled, remove the optional local access helper first with the command above. You can then remove the extracted source directory. Connection settings remain available unless you remove them separately.

## Development

The source launcher runs directly from `src/`; no build or virtual environment is needed to run it with system GTK bindings.

```sh
python3 bin/nautilus-docker --help
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The GTK integration check uses mocked Docker responses and creates no visible window. Run it in a graphical session:

```sh
PYTHONPATH=src GSETTINGS_BACKEND=memory python3 tests/check_gtk.py
```

Build and validate release packages in a Python environment with `build` and `twine` installed:

```sh
python3 -m pip install build twine
python3 -m build
python3 -m twine check dist/*
python3 scripts/check_dist.py
```

CI runs unit tests on Python 3.11 and 3.14, checks the packaged installers and resources, and exercises the GTK interface under a virtual X display.

| Path | Purpose |
| --- | --- |
| `src/nautilus_docker/` | Application, resource queries and local helpers |
| `src/nautilus_docker/resources/` | View definitions and GTK styles |
| `bin/nautilus-docker` | Source launcher |
| `integration/` | Nautilus extension loader |
| `scripts/` | Installation, version preparation and package checks |
| `tests/` | Unit tests and GTK integration check |
| `.github/workflows/` | CI and release automation |

## Releases

This repository uses the same release PR flow as [AI Status](https://github.com/gelzinn/ai-status), with one Python package. The version in `src/nautilus_docker/__init__.py` also supplies the build metadata and `--version` output.

1. In repository **Settings → Actions → General → Workflow permissions**, allow GitHub Actions to create pull requests. The workflows request the required permissions.
2. Optionally add a `BOT_TOKEN` Actions secret with repository contents and pull request write access, as in AI Status. Without it, preparation uses `GITHUB_TOKEN`; GitHub may require approval before CI runs on the bot's PR. See [GitHub's workflow trigger rules](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow).
3. Run **Actions → Prepare Release → Run workflow** on `main`. Choose `auto`, `patch`, `minor` or `major`. The first release uses `0.1.0`; later automatic releases use Conventional Commits: breaking changes bump major, `feat` bumps minor, and other changes bump patch.
4. Review the `release/vX.Y.Z` PR and its CI checks. Use **Squash and merge**, keeping the `chore: release vX.Y.Z` title. GitHub's default `(#PR)` suffix is accepted.
5. The verified release commit triggers tests, then creates the matching tag and GitHub Release with generated notes, the source archive, wheel and `SHA256SUMS`.

Release preparation promotes the **Unreleased** section of [CHANGELOG.md](CHANGELOG.md). Existing release branches are preserved. Publication validates the package version and tag target. Reruns preserve complete published releases and recover interrupted drafts by uploading the release assets before publishing. An already-published release with missing assets fails with a message for the maintainer to review it. To retry a failed publication after merging, rerun its workflow; **Release** can also be dispatched on `main` while its latest commit is the release commit.

## License

[MIT](LICENSE) — Copyright © 2026 gelzin.com.
