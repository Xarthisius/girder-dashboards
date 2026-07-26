#!/usr/bin/env python3
"""Generate a synthetic micrograph for the tests to upload.

The real SEM micrographs the pipeline was developed against are large and
unpublished, so the tests use a synthetic stand-in: bright round "precipitates"
on a textured background, at a known count and spacing. That also makes it a
better test subject than real data — the expected answer is known.

Written with nothing but the standard library (a bare uncompressed 8-bit TIFF is
a header, one IFD and one strip) so that the browser harness's seed step stays
dependency-free, exactly like the rest of ``seed.py``.

    python3 test/browser/micrograph.py [output.tif]
"""

import math
import os
import random
import struct
import sys

WIDTH = 512
HEIGHT = 512

#: Grid of precipitates. Jittered off the lattice so nearest-neighbour spacing has
#: a distribution rather than a single value.
GRID = 8
JITTER = 9
RADIUS = 2.1

BACKGROUND = 38
PEAK = 255
NOISE = 7

#: Where seed.py puts it, and where verify.cjs looks for it.
DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "micrograph.tif"
)


def _blob(pixels, cx, cy, radius, peak):
    """Add one round Gaussian blob, clipped to the image."""
    reach = int(math.ceil(radius * 2.5))
    for y in range(max(0, int(cy) - reach), min(HEIGHT, int(cy) + reach + 1)):
        for x in range(max(0, int(cx) - reach), min(WIDTH, int(cx) + reach + 1)):
            distSq = (x - cx) ** 2 + (y - cy) ** 2
            value = peak * math.exp(-distSq / (2 * (radius * 0.62) ** 2))
            index = y * WIDTH + x
            pixels[index] = min(255, pixels[index] + int(value))


def render(seed=20260726):
    """Return ``(pixels, centres)``: the 8-bit image and the true blob centres."""
    rng = random.Random(seed)

    # A little low-frequency shading plus per-pixel noise, so the detector has to
    # do its top-hat work rather than finding blobs in a flat field.
    pixels = bytearray(WIDTH * HEIGHT)
    for y in range(HEIGHT):
        shade = BACKGROUND + int(10 * math.sin(y / 90.0))
        for x in range(WIDTH):
            pixels[y * WIDTH + x] = max(
                0,
                min(
                    255,
                    shade + int(6 * math.sin(x / 70.0)) + rng.randint(-NOISE, NOISE),
                ),
            )

    centres = []
    step = WIDTH // GRID
    for row in range(GRID):
        for column in range(GRID):
            cx = step * (column + 0.5) + rng.uniform(-JITTER, JITTER)
            cy = step * (row + 0.5) + rng.uniform(-JITTER, JITTER)
            _blob(pixels, cx, cy, RADIUS, PEAK - BACKGROUND)
            centres.append((cx, cy))

    return pixels, centres


def write(path=DEFAULT_PATH, seed=20260726):
    """Write the micrograph as an uncompressed 8-bit greyscale TIFF."""
    pixels, centres = render(seed)

    # tag, type (3=SHORT, 4=LONG), count, value. Tags must be in ascending order.
    entries = [
        (256, 3, 1, WIDTH),  # ImageWidth
        (257, 3, 1, HEIGHT),  # ImageLength
        (258, 3, 1, 8),  # BitsPerSample
        (259, 3, 1, 1),  # Compression: none
        (262, 3, 1, 1),  # PhotometricInterpretation: BlackIsZero
        (273, 4, 1, 0),  # StripOffsets, filled in below
        (277, 3, 1, 1),  # SamplesPerPixel
        (278, 3, 1, HEIGHT),  # RowsPerStrip: the whole image
        (279, 4, 1, WIDTH * HEIGHT),  # StripByteCounts
    ]
    # Header (8) + entry count (2) + entries (12 each) + next-IFD offset (4).
    dataOffset = 8 + 2 + len(entries) * 12 + 4
    entries = [
        (tag, kind, count, dataOffset if tag == 273 else value)
        for tag, kind, count, value in entries
    ]

    out = bytearray()
    out += struct.pack("<2sHI", b"II", 42, 8)
    out += struct.pack("<H", len(entries))
    for tag, kind, count, value in entries:
        # A SHORT value is left-justified in its 4-byte field.
        packed = struct.pack("<HH", value, 0) if kind == 3 else struct.pack("<I", value)
        out += struct.pack("<HHI", tag, kind, count) + packed
    out += struct.pack("<I", 0)
    out += pixels

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(out)

    return path, len(centres)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    written, count = write(target)
    print(f"wrote {written} ({WIDTH}x{HEIGHT}, {count} precipitates)")
