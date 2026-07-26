"""Turn a micrograph into something a browser can display.

Browsers cannot render the LZW-compressed, sometimes 16-bit TIFFs that SEM
software writes, but the user has to *see* the image to draw regions of interest
on it. So one backend step decodes the micrograph and writes a downscaled PNG
next to it in the run folder.

The PNG is rendered from :py:func:`~.analysis.loadImage`'s output rather than
from the raw file, which means the user picks regions on exactly the grey values
the detector will work from — no second decode path to disagree with the first.
"""

import io

#: Longest edge of the generated preview. Big enough that a 2-3 px precipitate is
#: still visible when the browser scales the PNG back up, small enough to stay a
#: few hundred kB.
MAX_PREVIEW_EDGE = 1400


def renderPreview(path, maxEdge=MAX_PREVIEW_EDGE):
    """Render ``path`` as a grey PNG, downscaled to fit ``maxEdge``.

    :returns: ``(pngBytes, info)`` where ``info`` carries the full-resolution
        dimensions and the preview's own dimensions. The client needs both to map
        a rectangle drawn on the preview back to full-resolution pixels.
    """
    import numpy as np
    from PIL import Image

    from .analysis import loadImage

    gray = loadImage(path)
    height, width = gray.shape

    image = Image.fromarray(np.round(gray * 255.0).astype(np.uint8), mode="L")

    # Never upscale: a small micrograph is shown at its own resolution and the
    # browser stretches it, rather than shipping interpolated pixels.
    scale = min(1.0, maxEdge / max(width, height))
    if scale < 1.0:
        image = image.resize(
            (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
            Image.LANCZOS,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)

    return buffer.getvalue(), {
        "width": int(width),
        "height": int(height),
        "previewWidth": image.width,
        "previewHeight": image.height,
    }
