"""A URL as a QR picture for the sign-in screen (no Enigma2 imports; segno is bundled)."""
from .vendor import segno


def matrix(url: str) -> list:
    code = segno.make(url, error="m", micro=False)
    return [[bool(cell) for cell in row] for row in code.matrix]


def png_for(url: str, path: str, size: int):
    """Black on white, 4-module quiet zone, nearest-neighbour scaled to size x size. None on failure."""
    try:
        from PIL import Image
        rows = matrix(url)
        n = len(rows) + 8
        image = Image.new("RGB", (n, n), (255, 255, 255))
        pixels = image.load()
        for y, row in enumerate(rows):
            for x, dark in enumerate(row):
                if dark:
                    pixels[x + 4, y + 4] = (0, 0, 0)
        nearest = getattr(Image, "Resampling", Image).NEAREST
        image.resize((size, size), nearest).save(path, "PNG")
    except Exception:  # noqa: BLE001 - no picture: the screen shows the code as text
        return None
    return path
