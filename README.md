# ProtonAutoPaths

A Windows GUI tool that automatically scans directories for executables and updates [Proton VPN](https://protonvpn.com/)'s split-tunneling configuration.

## Features

- **Folder based** – every `.exe` inside the folders you pick (subfolders included) is added; single apps can be picked too
- **Two split-tunneling modes**
  - `exclude` (Standard) – listed apps bypass the VPN
  - `include` (Inverse) – only listed apps use the VPN
- **Skip folders** – skip subtrees (e.g. Proton VPN's own install directory) so the VPN client itself is never routed through its own tunnel
- **Run at logon** – one checkbox adds/removes a Windows startup entry (no admin rights needed)
- **Dark/light theme** auto-detected via `darkdetect` and styled with `sv_ttk`

## Requirements

- Windows 10/11
- Proton VPN installed (desktop client)
- Python 3.10+ **or** the pre-built `.exe` from the [Releases](../../releases) page

Optional Python packages (theme only):
```
sv_ttk
darkdetect
```

## Usage

### Pre-built executable
Download `ProtonAutoPaths.exe` from the [Releases](../../releases) page and run it directly — no Python required.

### From source
```bash
pip install sv_ttk darkdetect
python main.py
```

### GUI walkthrough
1. **Proton config** – filled in automatically from `%LOCALAPPDATA%\Proton\Proton VPN\Storage\UserSettings.*.json`; browse only if it is wrong.
2. **Apps in folders** – choose whether they bypass the VPN or are the only ones using it.
3. **Folders / apps** – **Add** opens a file picker that shows apps and shortcuts (and follows shortcuts, e.g. from the Desktop). Select one or more, then choose whether to add just those apps or the whole folder they live in. **Add folder** is a plain folder picker.
4. **Skip** – add any subfolders or apps to leave out (Proton VPN's own folder is skipped by default).
5. Close Proton VPN, then click **Save + Apply**. Proton VPN overwrites the file on exit, so changes made while it is running are lost.

### Headless / startup mode
```bash
ProtonAutoPaths.exe --scan-and-apply
```
Applies the saved settings without a window, then launches Proton VPN. This is what the logon entry runs. Any other invocation opens the window.

## Settings

Settings are stored at `%LOCALAPPDATA%\ProtonAutoPaths\settings.json` and persist between sessions.

## License

See [LICENSE](LICENSE) if present, or check the repository for licensing information.
