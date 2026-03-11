# ProtonAutoPaths

A Windows GUI tool that automatically scans directories for executables and updates [Proton VPN](https://protonvpn.com/)'s split-tunneling configuration.

## Features

- **Auto-scan** folders (recursively or flat) for `.exe` files
- **Two split-tunneling modes**
  - `exclude` (Standard) – listed apps bypass the VPN
  - `include` (Inverse) – only listed apps use the VPN
- **Network-active filter** – optionally limit the list to executables that currently have active network connections (requires `psutil`)
- **Exclude folders** – skip subtrees (e.g. Proton VPN's own install directory) so the VPN client itself is never routed through its own tunnel
- **Run at startup** – register/unregister a Windows startup entry (no admin rights needed)
- **Dark/light theme** auto-detected via `darkdetect` and styled with `sv_ttk`

## Requirements

- Windows 10/11
- Proton VPN installed (desktop client)
- Python 3.10+ **or** the pre-built `.exe` from the [Releases](../../releases) page

Optional Python packages (installed automatically when using `pip`):
```
sv_ttk
darkdetect
psutil
```

## Usage

### Pre-built executable
Download `ProtonAutoPaths.exe` from the [Releases](../../releases) page and run it directly — no Python required.

### From source
```bash
pip install sv_ttk darkdetect psutil
python gui_launcher.py
```

### GUI walkthrough
1. **Proton config (JSON)** – browse to Proton VPN's `UserSettings.json` (usually `%LOCALAPPDATA%\ProtonVPN\UserSettings.json`).
2. **Scan paths** – add the folders you want to scan for executables (e.g. `C:\Program Files`).
3. **Exclude folders** – add any folders to skip (Proton VPN's own folder is excluded by default).
4. **Mode** – choose `exclude` or `include`.
5. Click **Scan & Apply** – the tool closes Proton VPN if running, updates the config, and optionally re-launches it.

### Headless / startup mode
```bash
ProtonAutoPaths.exe --scan-and-apply
```
Run with `--scan-and-apply` to apply the saved settings silently (useful for the startup entry).

## Settings

Settings are stored at `%LOCALAPPDATA%\ProtonAutoPaths\settings.json` and persist between sessions.

## License

See [LICENSE](LICENSE) if present, or check the repository for licensing information.
