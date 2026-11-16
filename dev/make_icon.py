"""Draw assets/studio.ico: the studio's icon (static/icon.svg) — a white chip with a
magnifier over it, on the studio's blue rounded square. Standard library only.

Run from the project root: python dev/make_icon.py
"""
import struct
import zlib
from pathlib import Path

BLUE = (51, 85, 224)       # --accent
WHITE = (255, 255, 255)
SIZES = (16, 24, 32, 48, 64, 256)
SAMPLES = 4                 # per pixel side, for smooth edges
OUT = Path(__file__).resolve().parent.parent / "assets" / "studio.ico"

# All geometry in a 64 x 64 space (static/icon.svg is 32, doubled here).
CHIP = (17, 17, 38, 38)     # the chip's body (left, top, right, bottom)
CHIP_R = 5
STROKE = 1.9                # half-width of a white stroke
PINS = [(22.8, 17), (31.4, 17), (22.8, 38), (31.4, 38),   # top, bottom
        (17, 22.8), (17, 31.4), (38, 22.8), (38, 31.4)]   # left, right
PIN = 2.4                   # how far a pin sticks out
LOUPE = (39.2, 39.2, 9.0)   # the magnifier lens: centre x, y, radius
HANDLE = ((45.5, 45.5), (52, 52))
HANDLE_W = 2.2


def in_rounded(x, y, left, top, right, bottom, radius):
    if not (left <= x <= right and top <= y <= bottom):
        return False
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def near_segment(x, y, a, b, width):
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    length = dx * dx + dy * dy
    t = 0 if length == 0 else max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / length))
    px, py = x1 + t * dx, y1 + t * dy
    return (x - px) ** 2 + (y - py) ** 2 <= width ** 2


def colour_at(x, y):
    """Colour and coverage (0/1) of one point in the 64 x 64 drawing."""
    lx, ly, lr = LOUPE
    # the magnifier handle and lens ring (white), drawn on top
    if near_segment(x, y, *HANDLE, HANDLE_W):
        return WHITE, 1
    ring = (x - lx) ** 2 + (y - ly) ** 2
    if (lr - STROKE) ** 2 <= ring <= (lr + STROKE) ** 2:
        return WHITE, 1
    inside_lens = ring < (lr - STROKE) ** 2
    if inside_lens:
        return BLUE, 1  # clear lens over the chip
    # chip pins
    for cx, cy in PINS:
        if abs(x - cx) <= 0.9 and min(abs(y - CHIP[1]), abs(y - CHIP[3])) <= PIN and CHIP[1] - PIN <= y <= CHIP[3] + PIN:
            if cx in (22.8, 31.4):
                return WHITE, 1
        if abs(y - cy) <= 0.9 and min(abs(x - CHIP[0]), abs(x - CHIP[2])) <= PIN and CHIP[0] - PIN <= x <= CHIP[2] + PIN:
            if cy in (22.8, 31.4):
                return WHITE, 1
    # chip outline
    outer = in_rounded(x, y, CHIP[0] - STROKE, CHIP[1] - STROKE, CHIP[2] + STROKE, CHIP[3] + STROKE, CHIP_R + STROKE)
    inner = in_rounded(x, y, CHIP[0] + STROKE, CHIP[1] + STROKE, CHIP[2] - STROKE, CHIP[3] - STROKE, CHIP_R - STROKE)
    if outer and not inner:
        return WHITE, 1
    # background
    if in_rounded(x, y, 0, 0, 64, 64, 14):
        return BLUE, 1
    return BLUE, 0


def png(size):
    rows = bytearray()
    scale = 64 / size
    for py in range(size):
        rows.append(0)
        for px in range(size):
            red = green = blue = alpha = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    colour, inside = colour_at((px + (sx + 0.5) / SAMPLES) * scale, (py + (sy + 0.5) / SAMPLES) * scale)
                    red += colour[0] * inside
                    green += colour[1] * inside
                    blue += colour[2] * inside
                    alpha += inside
            total = SAMPLES * SAMPLES
            if alpha:
                rows += bytes((round(red / alpha), round(green / alpha), round(blue / alpha), round(255 * alpha / total)))
            else:
                rows += bytes(4)

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


def main():
    images = [png(size) for size in SIZES]
    offset = 6 + 16 * len(images)
    directory = b""
    for size, image in zip(SIZES, images):
        directory += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + directory + b"".join(images))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
