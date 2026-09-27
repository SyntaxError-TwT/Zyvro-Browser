"""Generate the Finder background from Zyvro's existing new-tab artwork."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "zyvro-background.png"
OUTPUT = ROOT / "packaging" / "Zyvro-dmg-background.png"
DMG_SIZE = (720, 450)


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Center-crop to the requested dimensions without stretching."""
    target_width, target_height = size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_width) // 2
    top = (resized.height - target_height) // 2
    return resized.crop(
        (left, top, left + target_width, top + target_height)
    )


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(f"Missing Zyvro new-tab background: {SOURCE}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    source = Image.open(SOURCE).convert("RGB")
    cover(source, DMG_SIZE).save(OUTPUT, optimize=True)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
