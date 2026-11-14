"""Add every .exe inside chosen folders to Proton VPN's split-tunneling list."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import winreg

try:
    import sv_ttk
except ImportError:
    sv_ttk = None

try:
    import darkdetect
except ImportError:
    darkdetect = None


_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))

# Always use the same settings.json regardless of where the exe is located.
SETTINGS_FILE = _LOCALAPPDATA / "ProtonAutoPaths" / "settings.json"
SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

_PROTON_STORAGE = _LOCALAPPDATA / "Proton" / "Proton VPN" / "Storage"
_PROTON_LAUNCHER = r"C:\Program Files\Proton\VPN\ProtonVPN.Launcher.exe"

# Proton VPN's own executables must never be routed through the tunnel they create.
_DEFAULT_SKIP_FOLDERS = [r"C:\Program Files\Proton"]

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "ProtonAutoPathsStartup"
_APPLY_FLAG = "--scan-and-apply"

# mode -> (Proton list key, Proton SplitTunnelingMode value)
_MODES = {
    "exclude": ("SplitTunnelingStandardAppsList", "Standard"),
    "include": ("SplitTunnelingInverseAppsList", "Inverse"),
}


def normalize_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(value))


# ── settings ─────────────────────────────────────────────────────────────────
def detect_proton_config() -> str:
    """Return the most recently written Proton VPN UserSettings file, or ''."""
    if not _PROTON_STORAGE.is_dir():
        return ""
    found = sorted(
        _PROTON_STORAGE.glob("UserSettings*.json"), key=lambda p: p.stat().st_mtime
    )
    return str(found[-1]) if found else ""


def default_settings() -> dict:
    return {
        "proton_config_path": "",
        "proton_exe_path": _PROTON_LAUNCHER,
        "launch_proton_after_apply": True,
        "scan_paths": [],
        "exclude_scan_folders": list(_DEFAULT_SKIP_FOLDERS),
        "mode": "exclude",
    }


def load_settings() -> dict:
    settings = default_settings()
    if SETTINGS_FILE.exists():
        with SETTINGS_FILE.open("r", encoding="utf-8-sig") as stream:
            saved = json.load(stream)
        # Keys from older versions (recursive, only_network_active) are dropped.
        settings.update({k: v for k, v in saved.items() if k in settings})
    if settings["mode"] not in _MODES:
        settings["mode"] = "exclude"
    if not settings["proton_config_path"]:
        settings["proton_config_path"] = detect_proton_config()
    return settings


def save_settings(settings: dict) -> None:
    with SETTINGS_FILE.open("w", encoding="utf-8") as stream:
        json.dump(settings, stream, indent=2)


# ── scan + apply ─────────────────────────────────────────────────────────────
def find_executables(folders: list[str], skip_folders: list[str]) -> list[str]:
    """Find every .exe inside each folder, skipping anything under skip_folders.

    Both lists may also name single .exe files.
    """
    skip = [normalize_path(f) for f in skip_folders if f]

    def skipped(path: Path) -> bool:
        norm = normalize_path(str(path))
        return any(norm == s or norm.startswith(s + os.sep) for s in skip)

    found = set()
    for entry in folders:
        base = Path(entry).expanduser()
        if base.is_dir():
            candidates = base.rglob("*.exe")
        elif base.suffix.lower() == ".exe":
            # An entry may also be a single app rather than a folder.
            candidates = [base]
        else:
            continue
        for exe in candidates:
            if exe.is_file() and not skipped(exe):
                found.add(str(exe.resolve()))
    return sorted(found)


def update_proton_config(config_path: str, exe_paths: list[str], mode: str) -> int:
    """Add exe_paths to Proton VPN's split-tunneling list; return how many were new.

    mode='exclude'  -> SplitTunnelingStandardAppsList  (apps that bypass VPN)
    mode='include'  -> SplitTunnelingInverseAppsList   (only these apps use VPN)
    The Proton VPN app must be closed before calling this, or it overwrites the
    file with its in-memory copy on exit.
    """
    config = Path(config_path)
    if not config_path or not config.exists():
        raise FileNotFoundError(f"Proton config file not found: {config_path!r}")

    with config.open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    list_key, vpn_mode = _MODES[mode]

    # The list is stored as a JSON-encoded *string* inside the settings dict.
    try:
        apps = json.loads(data.get(list_key) or "[]")
    except (json.JSONDecodeError, TypeError):
        apps = []
    if not isinstance(apps, list):
        apps = []

    known = {
        normalize_path(app["AppFilePath"])
        for app in apps
        if isinstance(app, dict) and "AppFilePath" in app
    }

    added = 0
    for exe in exe_paths:
        norm = normalize_path(exe)
        if norm in known:
            continue
        apps.append(
            {
                "AppFilePath": os.path.abspath(exe),
                "AlternateAppFilePaths": [],
                "IsActive": True,
            }
        )
        known.add(norm)
        added += 1

    data[list_key] = json.dumps(apps, separators=(",", ":"))
    data["SplitTunnelingMode"] = vpn_mode
    data["IsSplitTunnelingEnabled"] = "true"

    with config.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    return added


def scan_and_apply(settings: dict) -> tuple[int, int]:
    """Return (executables found, new entries added)."""
    exes = find_executables(settings["scan_paths"], settings["exclude_scan_folders"])
    added = update_proton_config(settings["proton_config_path"], exes, settings["mode"])
    return len(exes), added


def launch_proton(exe_path: str) -> None:
    if not Path(exe_path).exists():
        raise FileNotFoundError(f"Proton VPN launcher not found: {exe_path}")
    subprocess.Popen([exe_path], close_fds=True)


# ── run at logon ─────────────────────────────────────────────────────────────
def _startup_command() -> str:
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller exe — call it directly
        return f'"{sys.executable}" {_APPLY_FLAG}'
    # pythonw.exe has no console window
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python = pythonw if pythonw.exists() else sys.executable
    return f'"{python}" "{Path(__file__).resolve()}" {_APPLY_FLAG}'


def is_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
        return True
    except FileNotFoundError:
        return False


def set_startup(enabled: bool) -> None:
    """Add or remove the HKCU Run entry (per-user, no admin needed)."""
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass


def resolve_shortcut(path: str) -> str:
    """Return what a .lnk points to; any other path is returned unchanged.

    The Windows file dialog normally resolves shortcuts itself, so this only
    does work when it hands back the .lnk. WScript.Shell is used through
    PowerShell to avoid a pywin32 dependency.
    """
    if not path.lower().endswith(".lnk"):
        return path
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "(New-Object -ComObject WScript.Shell)"
            ".CreateShortcut($env:PAP_LNK).TargetPath",
        ],
        # -Command pastes extra arguments into the script text, so a path with
        # spaces or quotes would break it; pass the path through the environment.
        env={**os.environ, "PAP_LNK": path},
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.stdout.strip()


# ── UI ───────────────────────────────────────────────────────────────────────
class FolderList:
    """A labelled listbox of folders and single apps with Add / Remove buttons."""

    def __init__(self, parent: ttk.Frame, row: int, label: str):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="nw", pady=(10, 0))
        self.listbox = tk.Listbox(parent, height=6)
        self.listbox.grid(row=row, column=1, sticky="nsew", pady=(10, 0), padx=6)
        parent.rowconfigure(row, weight=1)

        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=2, sticky="n", pady=(10, 0))
        ttk.Button(buttons, text="Add", command=self.add).grid(sticky="ew", pady=2)
        ttk.Button(buttons, text="Add folder", command=self.add_folder).grid(
            sticky="ew", pady=2
        )
        ttk.Button(buttons, text="Remove", command=self.remove).grid(
            sticky="ew", pady=2
        )

    def get(self) -> list[str]:
        return list(self.listbox.get(0, tk.END))

    def set(self, folders: list[str]) -> None:
        self.listbox.delete(0, tk.END)
        for folder in folders:
            self.listbox.insert(tk.END, folder)

    def _insert(self, folder: str) -> None:
        known = {normalize_path(f) for f in self.get()}
        if normalize_path(folder) not in known:
            self.listbox.insert(tk.END, os.path.normpath(folder))

    def add_folder(self) -> None:
        folder = filedialog.askdirectory()
        if folder:
            self._insert(folder)

    def add(self) -> None:
        """Pick one or more apps (or shortcuts), then add them or their folder.

        Windows has no dialog that selects both files and folders, and the
        folder picker hides shortcuts. A file dialog shows shortcuts and follows
        them, so the user picks apps there and is then asked whether they meant
        the apps themselves or the whole folder they live in.
        """
        picked = filedialog.askopenfilenames(
            title="Pick apps or shortcuts",
            filetypes=[("Apps and shortcuts", "*.exe *.lnk")],
        )
        targets = []
        for path in picked:
            try:
                target = resolve_shortcut(path)
            except Exception as exc:
                messagebox.showerror("Error", f"Could not read shortcut:\n{exc}")
                return
            if os.path.isdir(target) or target.lower().endswith(".exe"):
                targets.append(os.path.normpath(target))
            else:
                messagebox.showerror(
                    "Error", f"Does not point to an app or folder:\n{path}"
                )
                return
        if not targets:
            return

        folders = list(
            dict.fromkeys(t if os.path.isdir(t) else os.path.dirname(t) for t in targets)
        )
        if folders == targets:  # only shortcuts to folders were picked
            whole_folder = True
        else:
            whole_folder = messagebox.askyesnocancel(
                "Add what?",
                "Add the whole folder (every app inside it)?\n\n"
                + "\n".join(folders)
                + "\n\nYes = the whole folder\nNo = only what you selected",
            )
            if whole_folder is None:
                return
        for entry in folders if whole_folder else targets:
            self._insert(entry)

    def remove(self) -> None:
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)


class ProtonAutoPathsUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Proton VPN Split Tunneling Auto Paths")
        self.settings = load_settings()

        frame = ttk.Frame(root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Proton config:").grid(row=0, column=0, sticky="w")
        self.config_var = tk.StringVar(value=self.settings["proton_config_path"])
        ttk.Entry(frame, textvariable=self.config_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(frame, text="Browse", command=self.pick_config).grid(row=0, column=2)

        ttk.Label(frame, text="Apps in folders:").grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        self.mode_var = tk.StringVar(value=self.settings["mode"])
        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0), padx=6)
        ttk.Radiobutton(
            mode_frame, text="bypass the VPN", variable=self.mode_var, value="exclude"
        ).grid(row=0, column=0)
        ttk.Radiobutton(
            mode_frame,
            text="are the only ones using the VPN",
            variable=self.mode_var,
            value="include",
        ).grid(row=0, column=1, padx=(12, 0))

        self.folders = FolderList(frame, 2, "Folders / apps:")
        self.folders.set(self.settings["scan_paths"])
        self.skipped = FolderList(frame, 3, "Skip:")
        self.skipped.set(self.settings["exclude_scan_folders"])

        self.launch_var = tk.BooleanVar(value=self.settings["launch_proton_after_apply"])
        ttk.Checkbutton(
            frame, text="Launch Proton VPN after applying", variable=self.launch_var
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        ttk.Checkbutton(
            frame,
            text="Apply at every Windows logon "
            "(remove Proton VPN from your startup apps so this runs first)",
            variable=self.startup_var,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.warn_label = ttk.Label(
            frame, text="⚠  Close Proton VPN before applying changes."
        )
        self.warn_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(actions, text="Save", command=self.save).grid(row=0, column=0)
        ttk.Button(actions, text="Save + Apply", command=self.apply).grid(
            row=0, column=1, padx=(6, 0)
        )

    def apply_theme(self, dark: bool) -> None:
        """Plain tk.Listbox is not styled by ttk themes, so colour it by hand."""
        bg, fg, select = (
            ("#1c1c1c", "#e0e0e0", "#3a3a3a") if dark else ("#ffffff", "#000000", "#0078d4")
        )
        for folder_list in (self.folders, self.skipped):
            folder_list.listbox.configure(
                bg=bg,
                fg=fg,
                selectbackground=select,
                selectforeground="#ffffff",
                highlightbackground=bg,
            )
        self.warn_label.configure(foreground="#ffaa44" if dark else "#cc6600")

    def pick_config(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=_PROTON_STORAGE if _PROTON_STORAGE.is_dir() else None,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.config_var.set(path)

    def _save(self) -> None:
        self.settings.update(
            {
                "proton_config_path": self.config_var.get().strip(),
                "launch_proton_after_apply": self.launch_var.get(),
                "scan_paths": self.folders.get(),
                "exclude_scan_folders": self.skipped.get(),
                "mode": self.mode_var.get(),
            }
        )
        save_settings(self.settings)
        set_startup(self.startup_var.get())

    def save(self) -> None:
        try:
            self._save()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        messagebox.showinfo("Saved", f"Settings saved to {SETTINGS_FILE}")

    def apply(self) -> None:
        try:
            self._save()
            found, added = scan_and_apply(self.settings)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        messagebox.showinfo(
            "Finished", f"Executables found: {found}\nNew entries added: {added}"
        )


def run_gui() -> None:
    root = tk.Tk()
    # icon.ico sits next to this file; in the PyInstaller exe that is the
    # unpack directory, where the spec bundles it.
    ico = Path(__file__).resolve().parent / "icon.ico"
    if ico.exists():
        root.iconbitmap(str(ico))

    ui = ProtonAutoPathsUI(root)

    # Apply Sun Valley theme based on system preference
    dark = bool(darkdetect and darkdetect.isDark())
    if sv_ttk:
        sv_ttk.set_theme("dark" if dark else "light")
    ui.apply_theme(dark)

    root.mainloop()


def run_headless() -> int:
    """Apply the saved settings without a window, then start Proton VPN."""
    settings = load_settings()
    found, added = scan_and_apply(settings)
    print(f"Executables found: {found}")
    print(f"New entries added: {added}")
    if settings["launch_proton_after_apply"]:
        try:
            launch_proton(settings["proton_exe_path"])
        except Exception as exc:
            print(f"Warning: could not launch Proton VPN: {exc}")
    return 0


def main() -> int:
    """`--scan-and-apply` runs headless; anything else opens the window."""
    if _APPLY_FLAG in sys.argv[1:]:
        return run_headless()
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
