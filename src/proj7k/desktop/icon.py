"""
The app icon, drawn rather than shipped as a binary: `python3 -m proj7k.desktop.icon out.ico` writes a
multi-size .ico (the shortcut's icon) with Pillow.
"""

from pathlib import Path
import sys

from PIL import Image, ImageDraw

#: The dashboard's own gradient (app.css --accent-blue to --accent-purple) behind seven key columns.
_TOP, _BOTTOM = (37, 99, 235), (124, 58, 237)


def draw(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / (size - 1)
        gd.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(_TOP, _BOTTOM)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 5, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    # Seven keys of a 7K playfield, with notes falling toward four of them.
    pad, gap = size * 0.14, size * 0.025
    w = (size - 2 * pad - 6 * gap) / 7
    for i in range(7):
        x0 = pad + i * (w + gap)
        d.rounded_rectangle([x0, size * 0.62, x0 + w, size * 0.82], radius=w / 3, fill=(255, 255, 255, 235))
        if i in (0, 2, 4, 6):
            y = size * (0.22 + 0.08 * (i % 4 == 0))
            d.rounded_rectangle([x0, y, x0 + w, y + w * 0.7], radius=w / 4, fill=(255, 255, 255, 255))
    return img


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    out = Path(args[0] if args else "proj7k.ico")
    out.parent.mkdir(parents=True, exist_ok=True)
    draw(256).save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
