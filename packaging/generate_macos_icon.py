"""Create Zyvro.icns from the existing transparent Zyvro logo.

The script uses macOS' iconutil because that produces the native icon resource
Finder and the Dock expect. It is intentionally separate from the Windows
installer-art generator.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "zyvro-logo.png"
ICONSET = ROOT / "build" / "Zyvro.iconset"
OUTPUT = ROOT / "packaging" / "Zyvro.icns"


def render_icon(size: int) -> Image.Image:
    source = Image.open(SOURCE).convert("RGBA")
    alpha_box = source.getchannel("A").getbbox()
    if alpha_box:
        source = source.crop(alpha_box)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    source.thumbnail((round(size * 0.90), round(size * 0.90)), Image.Resampling.LANCZOS)
    canvas.alpha_composite(
        source,
        ((size - source.width) // 2, (size - source.height) // 2),
    )
    return canvas


def main() -> int:
    if sys.platform != "darwin":
        raise SystemExit("Zyvro.icns must be generated on macOS with iconutil.")
    if not SOURCE.is_file():
        raise SystemExit(f"Missing logo: {SOURCE}")

    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # Apple iconsets pair each logical size with its @2x representation.
    for logical in (16, 32, 128, 256, 512):
        render_icon(logical).save(ICONSET / f"icon_{logical}x{logical}.png")
        render_icon(logical * 2).save(
            ICONSET / f"icon_{logical}x{logical}@2x.png"
        )

    subprocess.run(
        ["iconutil", "--convert", "icns", str(ICONSET), "--output", str(OUTPUT)],
        check=True,
    )
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
