import argparse
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

try:
    import psutil
except ImportError:
    psutil = None


# Always use the same settings.json regardless of where the exe is located.
SETTINGS_FILE = (
    Path(os.environ.get("LOCALAPPDATA", "~")) / "ProtonAutoPaths" / "settings.json"
)
SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)


def normalize_path(value: str) -> str:
    return os.path.normcase(os.path.abspath(value))


_TASK_NAME = "ProtonAutoPathsStartup"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _pythonw() -> str:
    """Return pythonw.exe path (no console window) next to the running Python."""
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw) if pyw.exists() else sys.executable


def launch_proton(exe_path: str) -> None:
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"Proton VPN launcher not found: {exe_path}")
    subprocess.Popen([str(path)], close_fds=True)


def _startup_command() -> str:
    """Return the command string to write into the Run registry key."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller exe — call it directly
        return f'"{sys.executable}" --scan-and-apply'
    else:
        script = str(Path(__file__).resolve())
        python = _pythonw()
        return f'"{python}" "{script}" --scan-and-apply'


def register_startup_task() -> None:
    """Add an HKCU Run entry so the tool runs at every user logon (no admin needed)."""
    cmd = _startup_command()
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, _TASK_NAME, 0, winreg.REG_SZ, cmd)


def unregister_startup_task() -> None:
    """Remove the HKCU Run entry."""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _TASK_NAME)
    except FileNotFoundError:
        raise RuntimeError(
            f"Startup entry '{_TASK_NAME}' not found — it may not be registered."
        )


# Proton VPN's own executables must never be routed through the tunnel they create.
_DEFAULT_EXCLUDE_FOLDERS: list[str] = [
    r"C:\Program Files\Proton",
]


def default_settings() -> dict:
    return {
        "proton_config_path": "",
        "proton_exe_path": r"C:\Program Files\Proton\VPN\ProtonVPN.Launcher.exe",
        "launch_proton_after_apply": True,
        "scan_paths": [],
        "exclude_scan_folders": list(_DEFAULT_EXCLUDE_FOLDERS),
        "mode": "exclude",
        "recursive": True,
        "only_network_active": True,
    }


def load_settings() -> dict:
    defaults = default_settings()
    if not SETTINGS_FILE.exists():
        return defaults

    with SETTINGS_FILE.open("r", encoding="utf-8-sig") as stream:
        saved = json.load(stream)

    defaults.update(saved)
    if defaults["mode"] not in {"include", "exclude"}:
        defaults["mode"] = "exclude"
    return defaults


def save_settings(settings: dict) -> None:
    with SETTINGS_FILE.open("w", encoding="utf-8") as stream:
        json.dump(settings, stream, indent=2)


def find_executables(
    paths: list[str],
    recursive: bool = True,
    exclude_folders: list[str] | None = None,
) -> list[str]:
    """Find .exe files under each path, skipping any exclude_folders subtree."""
    exclude_norm: set[str] = set()
    for ef in exclude_folders or []:
        if ef:
            exclude_norm.add(normalize_path(ef))

    def _is_excluded(p: Path) -> bool:
        norm = normalize_path(str(p))
        for excl in exclude_norm:
            # True if p is inside (or equal to) an excluded folder
            if norm == excl or norm.startswith(excl + os.sep):
                return True
        return False

    found = set()
    for base in paths:
        if not base:
            continue
        base_path = Path(base).expanduser()
        if not base_path.exists() or not base_path.is_dir():
            continue
        if _is_excluded(base_path):
            continue

        iterator = base_path.rglob("*.exe") if recursive else base_path.glob("*.exe")
        for exe in iterator:
            if exe.is_file() and not _is_excluded(exe.parent):
                found.add(str(exe.resolve()))
    return sorted(found)


def get_network_active_executables() -> set[str] | None:
    if psutil is None:
        return None

    exes = set()
    for connection in psutil.net_connections(kind="inet"):
        if connection.pid is None:
            continue
        if not connection.raddr:
            continue
        try:
            proc = psutil.Process(connection.pid)
            proc_exe = proc.exe()
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.ZombieProcess,
            OSError,
        ):
            continue
        if proc_exe:
            exes.add(normalize_path(proc_exe))
    return exes


# Proton VPN UserSettings.json key names
_MODE_MAP = {
    "exclude": ("SplitTunnelingStandardAppsList", "Standard"),
    "include": ("SplitTunnelingInverseAppsList", "Inverse"),
}


def _parse_app_list(raw: str) -> list[dict]:
    """Parse the JSON-encoded app list string from Proton VPN UserSettings.json."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def update_proton_config(
    config_path: str, exe_paths: list[str], mode: str = "exclude"
) -> int:
    """Add exe_paths to the Proton VPN UserSettings.json split-tunneling list.

    mode='exclude'  -> SplitTunnelingStandardAppsList  (apps that bypass VPN)
    mode='include'  -> SplitTunnelingInverseAppsList   (only these apps use VPN)
    The Proton VPN app must be closed before calling this.
    """
    if mode not in _MODE_MAP:
        raise ValueError("mode must be 'include' or 'exclude'")

    config = Path(config_path)
    if not config.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config.open("r", encoding="utf-8") as stream:
        profile_data = json.load(stream)

    list_key, vpn_mode_value = _MODE_MAP[mode]

    # The list is stored as a JSON-encoded *string* inside the settings dict
    existing_list = _parse_app_list(profile_data.get(list_key, "[]"))

    # Build lookup of already-present paths (normalised)
    existing_norm: set[str] = set()
    for entry in existing_list:
        if isinstance(entry, dict) and "AppFilePath" in entry:
            existing_norm.add(normalize_path(entry["AppFilePath"]))

    added_count = 0
    for exe in exe_paths:
        norm = normalize_path(exe)
        if norm not in existing_norm:
            existing_list.append(
                {
                    "AppFilePath": os.path.abspath(exe),
                    "AlternateAppFilePaths": [],
                    "IsActive": True,
                }
            )
            existing_norm.add(norm)
            added_count += 1

    # Store back as a compact JSON-encoded string (matches Proton VPN's own format)
    profile_data[list_key] = json.dumps(existing_list, separators=(",", ":"))
    profile_data["SplitTunnelingMode"] = vpn_mode_value
    profile_data["IsSplitTunnelingEnabled"] = "true"

    # Write back preserving Proton VPN's compact outer format (no indentation)
    with config.open("w", encoding="utf-8") as stream:
        json.dump(profile_data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

    return added_count


def scan_and_apply(settings: dict) -> tuple[int, int]:
    if not settings.get("proton_config_path"):
        raise ValueError("'proton_config_path' is empty in settings.json")

    scan_paths = settings.get("scan_paths", [])
    exclude_folders = settings.get(
        "exclude_scan_folders", list(_DEFAULT_EXCLUDE_FOLDERS)
    )
    recursive = bool(settings.get("recursive", True))
    only_network_active = bool(settings.get("only_network_active", True))
    mode = settings.get("mode", "exclude")

    all_exes = find_executables(
        scan_paths, recursive=recursive, exclude_folders=exclude_folders
    )
    filtered = all_exes

    if only_network_active:
        network_exes = get_network_active_executables()
        if network_exes is not None:
            filtered = [exe for exe in all_exes if normalize_path(exe) in network_exes]

    added = update_proton_config(settings["proton_config_path"], filtered, mode=mode)
    return len(filtered), added


# Colour palette used by the listboxes (updated by _apply_listbox_theme)
_LB_DARK = {
    "bg": "#1c1c1c",
    "fg": "#e0e0e0",
    "selectbg": "#3a3a3a",
    "selectfg": "#ffffff",
}
_LB_LIGHT = {
    "bg": "#ffffff",
    "fg": "#000000",
    "selectbg": "#0078d4",
    "selectfg": "#ffffff",
}


class ProtonAutoPathsUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Proton VPN Split Tunneling Auto Paths")
        self.settings = load_settings()
        self._build_ui()
        self._load_settings_to_ui()

    def _build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        self._listboxes: list[tk.Listbox] = []

        ttk.Label(frame, text="Proton config (JSON):").grid(row=0, column=0, sticky="w")
        self.config_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.config_var).grid(
            row=0, column=1, sticky="ew", padx=6
        )
        ttk.Button(frame, text="Browse", command=self.pick_config).grid(row=0, column=2)

        ttk.Label(frame, text="Mode:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.mode_var = tk.StringVar(value="exclude")
        mode_frame = ttk.Frame(frame)
        mode_frame.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Combobox(
            mode_frame,
            textvariable=self.mode_var,
            values=["exclude", "include"],
            state="readonly",
            width=12,
        ).grid(row=0, column=0)
        self._mode_hint = ttk.Label(
            mode_frame,
            text="  exclude = Standard (apps bypass VPN)   include = Inverse (only these apps use VPN)",
        )
        self._mode_hint.grid(row=0, column=1, sticky="w", padx=(6, 0))

        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Recursive scan", variable=self.recursive_var).grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

        self.network_only_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Only running apps with network activity",
            variable=self.network_only_var,
        ).grid(row=2, column=1, sticky="w", pady=(8, 0))

        ttk.Label(frame, text="Scan paths:").grid(
            row=3, column=0, sticky="nw", pady=(10, 0)
        )
        self.paths_list = tk.Listbox(frame, height=6)
        self._listboxes.append(self.paths_list)
        self.paths_list.grid(row=3, column=1, sticky="nsew", pady=(10, 0), padx=6)
        frame.rowconfigure(3, weight=1)

        btn_col = ttk.Frame(frame)
        btn_col.grid(row=3, column=2, sticky="ns", pady=(10, 0))
        ttk.Button(btn_col, text="Add Path", command=self.add_path).grid(
            row=0, column=0, sticky="ew", pady=2
        )
        ttk.Button(btn_col, text="Remove Selected", command=self.remove_path).grid(
            row=1, column=0, sticky="ew", pady=2
        )

        ttk.Label(frame, text="Exclude folders:").grid(
            row=4, column=0, sticky="nw", pady=(10, 0)
        )
        self.exclude_list = tk.Listbox(frame, height=5)
        self._listboxes.append(self.exclude_list)
        self.exclude_list.grid(row=4, column=1, sticky="nsew", pady=(10, 0), padx=6)
        frame.rowconfigure(4, weight=1)
        self._excl_hint = ttk.Label(
            frame,
            text="(subfolders of scan paths to skip — Proton VPN dir excluded by default)",
        )
        self._excl_hint.grid(row=5, column=1, sticky="w", padx=6)

        excl_btn_col = ttk.Frame(frame)
        excl_btn_col.grid(row=4, column=2, sticky="ns", pady=(10, 0))
        ttk.Button(
            excl_btn_col, text="Add Exclude", command=self.add_exclude_path
        ).grid(row=0, column=0, sticky="ew", pady=2)
        ttk.Button(
            excl_btn_col, text="Remove Selected", command=self.remove_exclude_path
        ).grid(row=1, column=0, sticky="ew", pady=2)

        self._warn_label = ttk.Label(
            frame,
            text="⚠  Close Proton VPN before applying changes.",
        )
        self._warn_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # --- Proton launcher + startup ---
        ttk.Label(frame, text="Proton launcher:").grid(
            row=7, column=0, sticky="w", pady=(8, 0)
        )
        self.proton_exe_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.proton_exe_var).grid(
            row=7, column=1, sticky="ew", padx=6, pady=(8, 0)
        )
        ttk.Button(frame, text="Browse", command=self.pick_proton_exe).grid(
            row=7, column=2, pady=(8, 0)
        )

        self.launch_after_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Launch Proton VPN after applying (recommended for startup use)",
            variable=self.launch_after_var,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 0))

        startup_row = ttk.Frame(frame)
        startup_row.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        ttk.Button(
            startup_row,
            text="Register Startup Task",
            command=self.register_startup,
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(
            startup_row,
            text="Unregister Startup Task",
            command=self.unregister_startup,
        ).grid(row=0, column=1)
        self._startup_hint = ttk.Label(
            startup_row,
            text="  Runs --scan-and-apply at every Windows logon (remove Proton VPN from startup apps)",
        )
        self._startup_hint.grid(row=0, column=2, sticky="w", padx=(6, 0))

        action_row = ttk.Frame(frame)
        action_row.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        action_row.columnconfigure(2, weight=1)
        ttk.Button(
            action_row, text="Add EXE Manually", command=self.add_exe_manually
        ).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(action_row, text="Save Settings", command=self.save_from_ui).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(
            action_row, text="Scan + Apply", command=self.scan_apply_from_ui
        ).grid(row=0, column=2, sticky="e")

    def _apply_listbox_theme(self, dark: bool) -> None:
        pal = _LB_DARK if dark else _LB_LIGHT
        hint_fg = "#aaaaaa" if dark else "#888888"
        warn_fg = "#ffaa44" if dark else "#cc6600"
        for lb in self._listboxes:
            lb.configure(
                bg=pal["bg"],
                fg=pal["fg"],
                selectbackground=pal["selectbg"],
                selectforeground=pal["selectfg"],
                highlightbackground=pal["bg"],
            )
        for lbl in (self._mode_hint, self._excl_hint, self._startup_hint):
            lbl.configure(foreground=hint_fg)
        self._warn_label.configure(foreground=warn_fg)

    def _load_settings_to_ui(self):
        self.config_var.set(self.settings.get("proton_config_path", ""))
        self.proton_exe_var.set(
            self.settings.get(
                "proton_exe_path", r"C:\Program Files\Proton\VPN\ProtonVPN.Launcher.exe"
            )
        )
        self.launch_after_var.set(
            bool(self.settings.get("launch_proton_after_apply", True))
        )
        self.mode_var.set(self.settings.get("mode", "exclude"))
        self.recursive_var.set(bool(self.settings.get("recursive", True)))
        self.network_only_var.set(bool(self.settings.get("only_network_active", True)))

        self.paths_list.delete(0, tk.END)
        for path in self.settings.get("scan_paths", []):
            self.paths_list.insert(tk.END, path)

        self.exclude_list.delete(0, tk.END)
        excl = self.settings.get("exclude_scan_folders", list(_DEFAULT_EXCLUDE_FOLDERS))
        for path in excl:
            self.exclude_list.insert(tk.END, path)

    def _collect_paths_from_ui(self) -> list[str]:
        return [self.paths_list.get(i) for i in range(self.paths_list.size())]

    def _collect_excludes_from_ui(self) -> list[str]:
        return [self.exclude_list.get(i) for i in range(self.exclude_list.size())]

    def _settings_from_ui(self) -> dict:
        return {
            "proton_config_path": self.config_var.get().strip(),
            "proton_exe_path": self.proton_exe_var.get().strip(),
            "launch_proton_after_apply": self.launch_after_var.get(),
            "scan_paths": self._collect_paths_from_ui(),
            "exclude_scan_folders": self._collect_excludes_from_ui(),
            "mode": self.mode_var.get(),
            "recursive": self.recursive_var.get(),
            "only_network_active": self.network_only_var.get(),
        }

    def pick_config(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.config_var.set(path)

    def pick_proton_exe(self):
        path = filedialog.askopenfilename(
            title="Select Proton VPN launcher",
            filetypes=[("Executables", "*.exe")],
        )
        if path:
            self.proton_exe_var.set(path)

    def register_startup(self):
        try:
            save_settings(self._settings_from_ui())
            register_startup_task()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        messagebox.showinfo(
            "Registered",
            f"Task '{_TASK_NAME}' registered.\n\nRemember to remove Proton VPN from your startup apps so this script runs first.",
        )

    def unregister_startup(self):
        try:
            unregister_startup_task()
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        messagebox.showinfo("Unregistered", f"Task '{_TASK_NAME}' removed.")

    def add_path(self):
        path = filedialog.askdirectory()
        if not path:
            return
        existing = self._collect_paths_from_ui()
        if path not in existing:
            self.paths_list.insert(tk.END, path)

    def remove_path(self):
        selected = list(self.paths_list.curselection())
        for index in reversed(selected):
            self.paths_list.delete(index)

    def add_exclude_path(self):
        path = filedialog.askdirectory(title="Select folder to exclude from scan")
        if not path:
            return
        existing = self._collect_excludes_from_ui()
        if path not in existing:
            self.exclude_list.insert(tk.END, path)

    def remove_exclude_path(self):
        selected = list(self.exclude_list.curselection())
        for index in reversed(selected):
            self.exclude_list.delete(index)

    def save_from_ui(self):
        settings = self._settings_from_ui()
        save_settings(settings)
        self.settings = settings
        messagebox.showinfo("Saved", f"Settings saved to {SETTINGS_FILE}")

    def add_exe_manually(self):
        config_path = self.config_var.get().strip()
        mode = self.mode_var.get().strip()
        exe_path = filedialog.askopenfilename(filetypes=[("Executables", "*.exe")])
        if not exe_path:
            return
        try:
            added = update_proton_config(config_path, [exe_path], mode=mode)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        messagebox.showinfo("Done", f"Added {added} executable(s).")

    def scan_apply_from_ui(self):
        settings = self._settings_from_ui()
        try:
            save_settings(settings)
            matched_count, added_count = scan_and_apply(settings)
        except Exception as exc:
            messagebox.showerror("Error", str(exc))
            return
        messagebox.showinfo(
            "Finished",
            f"Matched executables: {matched_count}\nNew entries added: {added_count}",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Proton VPN split tunneling auto path updater"
    )
    parser.add_argument("--ui", action="store_true", help="Launch Tkinter UI")
    parser.add_argument(
        "--scan-and-apply",
        action="store_true",
        help="Run scan and update once using settings",
    )
    parser.add_argument("--config", help="Override Proton config JSON path")
    parser.add_argument(
        "--mode", choices=["include", "exclude"], help="Split tunneling mode"
    )
    parser.add_argument(
        "--add-path", action="append", help="Add scan path to settings (can repeat)"
    )
    parser.add_argument(
        "--remove-path",
        action="append",
        help="Remove scan path from settings (can repeat)",
    )
    parser.add_argument(
        "--network-only",
        action="store_true",
        help="Only add executables with active network connections",
    )
    parser.add_argument(
        "--all-exes", action="store_true", help="Disable network-only filter"
    )
    parser.add_argument(
        "--non-recursive", action="store_true", help="Scan only top-level directory"
    )
    parser.add_argument(
        "--save-only",
        action="store_true",
        help="Apply CLI overrides and save settings only",
    )
    return parser.parse_args()


def apply_cli_overrides(settings: dict, args: argparse.Namespace) -> dict:
    merged = dict(settings)

    if args.config:
        merged["proton_config_path"] = args.config
    if args.mode:
        merged["mode"] = args.mode
    if args.non_recursive:
        merged["recursive"] = False
    if args.network_only:
        merged["only_network_active"] = True
    if args.all_exes:
        merged["only_network_active"] = False

    paths = list(merged.get("scan_paths", []))
    if args.add_path:
        for path in args.add_path:
            if path not in paths:
                paths.append(path)
    if args.remove_path:
        remove_set = set(args.remove_path)
        paths = [path for path in paths if path not in remove_set]
    merged["scan_paths"] = paths

    return merged


def run_gui() -> None:
    root = tk.Tk()
    ico = SETTINGS_FILE.parent / "icon.ico"
    if ico.exists():
        root.iconbitmap(str(ico))

    ui = ProtonAutoPathsUI(root)

    # Apply Sun Valley theme based on system preference
    dark = (darkdetect.isDark() if darkdetect else False) or False
    if sv_ttk:
        sv_ttk.set_theme("dark" if dark else "light")
    ui._apply_listbox_theme(dark)

    root.mainloop()


def run_click_and_run() -> int:
    settings = load_settings()
    matched_count, added_count = scan_and_apply(settings)
    print(f"Matched executables: {matched_count}")
    print(f"New entries added: {added_count}")
    if settings.get("only_network_active", True) and psutil is None:
        print("Note: Install 'psutil' for accurate network-activity filtering.")
    if settings.get("launch_proton_after_apply", True):
        exe = settings.get(
            "proton_exe_path", r"C:\Program Files\Proton\VPN\ProtonVPN.Launcher.exe"
        )
        try:
            launch_proton(exe)
            print(f"Launched: {exe}")
        except Exception as exc:
            print(f"Warning: could not launch Proton VPN: {exc}")
    return 0


def main() -> int:
    args = parse_args()
    settings = load_settings()
    updated_settings = apply_cli_overrides(settings, args)

    if updated_settings != settings or args.save_only:
        save_settings(updated_settings)

    if args.ui:
        run_gui()
        return 0

    if args.scan_and_apply or not any(vars(args).values()):
        matched_count, added_count = scan_and_apply(updated_settings)
        print(f"Matched executables: {matched_count}")
        print(f"New entries added: {added_count}")
        if updated_settings.get("only_network_active", True) and psutil is None:
            print("Note: Install 'psutil' for accurate network-activity filtering.")
        if updated_settings.get("launch_proton_after_apply", True):
            exe = updated_settings.get(
                "proton_exe_path", r"C:\Program Files\Proton\VPN\ProtonVPN.Launcher.exe"
            )
            try:
                launch_proton(exe)
                print(f"Launched: {exe}")
            except Exception as exc:
                print(f"Warning: could not launch Proton VPN: {exc}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
