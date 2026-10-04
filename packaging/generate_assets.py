"""Generate lossless Windows packaging media from the Zyvro source artwork."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
SOURCE_LOGO = ROOT / "assets" / "zyvro-logo.png"
SOURCE_BACKGROUND = ROOT / "assets" / "zyvro-background.png"
INSTALLER_SIDEBAR_SOURCE = ROOT / "installer" / "assets" / "installer-sidebar-source.png"
OUTPUT = ROOT / "installer" / "assets"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale and center-crop an image without stretching it."""
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - target_w) // 2
    top = (resized.height - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


def prepared_logo() -> Image.Image:
    logo = Image.open(SOURCE_LOGO).convert("RGBA")
    alpha_box = logo.getchannel("A").getbbox()
    if alpha_box:
        logo = logo.crop(alpha_box)
    side = max(logo.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.alpha_composite(
        logo, ((side - logo.width) // 2, (side - logo.height) // 2)
    )
    return canvas


def build_icon(logo: Image.Image) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    icon_source = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    fitted = logo.copy()
    fitted.thumbnail((920, 920), Image.Resampling.LANCZOS)
    icon_source.alpha_composite(
        fitted,
        ((icon_source.width - fitted.width) // 2,
         (icon_source.height - fitted.height) // 2),
    )
    icon_path = OUTPUT / "zyvro.ico"
    icon_source.save(icon_path, format="ICO", sizes=[(size, size) for size in ICON_SIZES])
    # Keep the runtime icon and installer icon generated from one source.
    icon_source.save(
        ROOT / "assets" / "zyvro.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )


def sidebar_art(background: Image.Image, logo: Image.Image, size: tuple[int, int]) -> Image.Image:
    panel = cover(background, size).convert("RGBA")
    panel = ImageEnhance.Brightness(panel).enhance(0.40)
    navy = Image.new("RGBA", size, (7, 11, 30, 115))
    panel = Image.alpha_composite(panel, navy)

    logo_copy = logo.copy()
    logo_copy.thumbnail((round(size[0] * 0.78), round(size[1] * 0.48)), Image.Resampling.LANCZOS)
    x = (size[0] - logo_copy.width) // 2
    y = round(size[1] * 0.38)

    # A soft, dramatic shadow travelling upward-left from the glossy Z.
    shadow_mask = logo_copy.getchannel("A").filter(
        ImageFilter.GaussianBlur(max(1, round(size[0] * 0.018)))
    )
    shadow_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    distance = round(size[1] * 0.42)
    steps = max(36, distance)
    for step in range(steps, 0, -1):
        ratio = step / steps
        offset_x = x - round(distance * 0.43 * ratio)
        offset_y = y - round(distance * ratio)
        opacity = max(1, round(5 * (1.0 - ratio * 0.45)))
        tint = Image.new("RGBA", logo_copy.size, (2, 4, 18, opacity))
        tint.putalpha(shadow_mask.point(lambda value, a=opacity: value * a // 255))
        shadow_layer.alpha_composite(tint, (offset_x, offset_y))
    panel = Image.alpha_composite(panel, shadow_layer)

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(
        (x - size[0] // 5, y - size[0] // 5,
         x + logo_copy.width + size[0] // 5,
         y + logo_copy.height + size[0] // 5),
        fill=(65, 41, 255, 74),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(max(8, size[0] // 7)))
    panel = Image.alpha_composite(panel, glow)
    panel.alpha_composite(logo_copy, (x, y))
    return panel.convert("RGB")


def installer_sidebar_art(size: tuple[int, int]) -> Image.Image:
    """Prepare the supplied installer portrait without adding or altering artwork."""
    source = Image.open(INSTALLER_SIDEBAR_SOURCE).convert("RGB")
    return cover(source, size)


def header_art(background: Image.Image, logo: Image.Image, size: tuple[int, int]) -> Image.Image:
    header = cover(background, size).convert("RGBA")
    header = ImageEnhance.Brightness(header).enhance(0.30)
    header = Image.alpha_composite(header, Image.new("RGBA", size, (7, 11, 30, 125)))

    accent = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(accent)
    draw.ellipse(
        (-size[0] // 5, -size[1], size[0] * 3 // 5, size[1] * 2),
        fill=(32, 203, 255, 80),
    )
    draw.ellipse(
        (size[0] // 2, -size[1], size[0] * 6 // 5, size[1] * 2),
        fill=(232, 61, 255, 72),
    )
    accent = accent.filter(ImageFilter.GaussianBlur(max(8, size[1] // 2)))
    header = Image.alpha_composite(header, accent)

    mark = logo.copy()
    mark.thumbnail((round(size[1] * 0.76), round(size[1] * 0.76)), Image.Resampling.LANCZOS)
    header.alpha_composite(mark, (size[0] - mark.width - size[1] // 6,
                                  (size[1] - mark.height) // 2))
    return header.convert("RGB")


def write_version_info(version: str) -> None:
    numeric = [int(part) for part in version.split(".") if part.isdigit()]
    numeric = (numeric + [0, 0, 0, 0])[:4]
    version_tuple = tuple(numeric)
    dotted = ".".join(str(part) for part in version_tuple)
    content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Zyvro'),
    StringStruct('FileDescription', 'Zyvro Browser'),
    StringStruct('FileVersion', '{dotted}'),
    StringStruct('InternalName', 'Zyvro'),
    StringStruct('LegalCopyright', 'Copyright (c) Zyvro'),
    StringStruct('OriginalFilename', 'Zyvro.exe'),
    StringStruct('ProductName', 'Zyvro'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    (ROOT / "packaging" / "zyvro_version_info.txt").write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="1.0.2")
    args = parser.parse_args()
    if (not SOURCE_LOGO.is_file() or not SOURCE_BACKGROUND.is_file()
            or not INSTALLER_SIDEBAR_SOURCE.is_file()):
        raise SystemExit("Zyvro logo, background, or installer portrait is missing.")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    logo = prepared_logo()
    background = Image.open(SOURCE_BACKGROUND).convert("RGB")
    build_icon(logo)

    # Match Inno Setup's native Welcome-page portrait ratio so the supplied
    # artwork is cropped gently instead of being stretched horizontally.
    for width, height in ((202, 386), (303, 579), (404, 772)):
        art = installer_sidebar_art((width, height))
        art.save(OUTPUT / f"installer-sidebar-{width}.png", optimize=True)
        art.save(OUTPUT / f"installer-sidebar-{width}.bmp", format="BMP")
    for width, height in ((55, 58), (83, 87), (110, 116)):
        art = header_art(background, logo, (width, height))
        art.save(OUTPUT / f"installer-header-{width}.png", optimize=True)
        art.save(OUTPUT / f"installer-header-{width}.bmp", format="BMP")

    # Human-friendly canonical names retain the highest-quality source media.
    installer_sidebar_art((808, 1544)).save(
        OUTPUT / "installer-sidebar.png", optimize=True
    )
    header_art(background, logo, (1200, 240)).save(
        OUTPUT / "installer-header.png", optimize=True
    )
    logo.save(OUTPUT / "zyvro-logo.png", optimize=True)
    write_version_info(args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
