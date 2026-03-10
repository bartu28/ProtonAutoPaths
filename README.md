# ProtonAutoPaths

A small Windows utility that automatically manages **Proton VPN split-tunneling** paths through a simple GUI.

## What it does

- Scans the folders you specify and collects executable paths.
- Writes those paths into the Proton VPN split-tunnel configuration file.
- Optionally launches Proton VPN after applying the configuration.
- Can be set to start automatically at Windows login.

## Requirements

- Windows 10/11
- [Proton VPN](https://protonvpn.com/) desktop client
- Python 3.10+ (if running from source) — or use the pre-built `.exe`

## Usage

Run `gui_launcher.py` (or the compiled `.exe`) to open the GUI, then:

1. Point the app to your Proton VPN config file and launcher executable.
2. Add the folders you want to include (or exclude) from split tunneling.
3. Click **Apply** — the paths are written and Proton VPN restarts automatically.

## Running from source

```bash
pip install sv-ttk darkdetect psutil
python gui_launcher.py
```
