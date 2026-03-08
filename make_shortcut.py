"""Generate app icon and create a desktop shortcut."""

import os
from pathlib import Path

from PIL import Image, ImageEnhance

# ── paths ────────────────────────────────────────────────────────────────────
APP_DIR = Path(__file__).parent
VBS = APP_DIR / "ProtonAutoPaths.vbs"
ICO_FILE = APP_DIR / "icon.ico"
PROTON_ICO = Path(r"C:\Program Files\Proton\VPN\v4.3.12\Assets\ProtonVPN.ico")
DESKTOP = Path(os.environ.get("USERPROFILE", "~")) / "Desktop"
LNK_FILE = DESKTOP / "ProtonAutoPaths.lnk"


# ── icon ─────────────────────────────────────────────────────────────────────
def darken_frame(img: Image.Image) -> Image.Image:
    """Convert to grayscale, boost contrast, then darken — keep full alpha."""
    img = img.convert("RGBA")
    r, g, b, a = img.split()
    gray = Image.merge("RGB", (r, g, b)).convert("L")
    gray = ImageEnhance.Contrast(gray.convert("RGB")).enhance(1.8)  # boost contrast
    gray = ImageEnhance.Brightness(gray).enhance(0.55)  # darken overall
    return Image.merge("RGBA", (*gray.split(), a))


def make_icon():
    sizes = [256, 128, 64, 48, 32, 16]
    src = Image.open(PROTON_ICO)
    frames = []

    for sz in sizes:
        # Try to get a frame at exactly this size, else resize from the largest
        try:
            src.size  # force load
            candidates = []
            for frame_idx in range(getattr(src, "n_frames", 1)):
                src.seek(frame_idx)
                candidates.append(src.copy().convert("RGBA"))
            # pick the frame whose size is closest to sz
            frame = min(candidates, key=lambda f: abs(f.width - sz))
        except Exception:
            frame = src.convert("RGBA")

        frame = frame.resize((sz, sz), Image.LANCZOS)
        frames.append(darken_frame(frame))

    frames[0].save(
        ICO_FILE, format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[1:]
    )
    print(f"Icon saved: {ICO_FILE}")


# ── shortcut ─────────────────────────────────────────────────────────────────
def make_shortcut():
    import subprocess

    ps = f"""
$ws  = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{LNK_FILE}')
$lnk.TargetPath       = '{VBS}'
$lnk.IconLocation     = '{ICO_FILE},0'
$lnk.Description      = 'Proton VPN Split Tunneling Auto Paths'
$lnk.WorkingDirectory = '{APP_DIR}'
$lnk.Save()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    print(f"Shortcut saved: {LNK_FILE}")


if __name__ == "__main__":
    make_icon()
    make_shortcut()
    print("Done!")
