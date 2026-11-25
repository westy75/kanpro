"""Posters downloaded once, resized to the tile, cached on the HDD (no Enigma2 imports)."""
import hashlib
import io
import os


def _fetch(url: str) -> bytes:
    import requests
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.content


def _resize(data: bytes, size) -> bytes:
    from PIL import Image
    image = Image.open(io.BytesIO(data)).convert("RGB")
    image = image.resize(size, Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def _round(data: bytes, size, radius: int) -> bytes:
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(data)).convert("RGB").resize(size, Image.LANCZOS)
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    image.putalpha(mask)                 # transparent corners: the skin blends it (alphatest="blend")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _card(data: bytes, size, radius: int, play: bool, progress: int) -> bytes:
    """Round a remote image and bake overlays; Pillow stays in this worker-only function."""
    from PIL import Image, ImageDraw
    image = Image.open(io.BytesIO(data)).convert("RGB").resize(size, Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image, "RGBA")
    if play:
        cx, cy, r = size[0] // 2, size[1] // 2, min(size) // 6
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 0, 0, 178))
        draw.polygon(((cx - r // 3, cy - r // 2), (cx - r // 3, cy + r // 2),
                      (cx + r // 2, cy)), fill=(255, 255, 255, 255))
    if progress:
        fill = int(size[0] * max(0, min(100, progress)) / 100.0)
        draw.rectangle((0, size[1] - 5, size[0], size[1] - 1), fill=(0, 0, 0, 190))
        draw.rectangle((0, size[1] - 5, fill, size[1] - 1), fill=(255, 82, 104, 255))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


SHADE_RGB, SHADE_SOLID, SHADE_REACH = (11, 12, 19), 0.3, 0.8   # theme.BG; solid 30 %, gone at 80 % (TV app)


def _shade(data: bytes, size, side: str) -> bytes:
    """The title page backdrop with the TV app's fade baked in: on the box a transparent widget pixel shows
    live TV, not the widget below, so the gradient cannot be a skin widget."""
    from PIL import Image
    image = Image.open(io.BytesIO(data)).convert("RGB").resize(size, Image.LANCZOS)
    width, height = size
    solid, reach = int(width * SHADE_SOLID), max(1, int(width * SHADE_REACH))
    mask = Image.new("L", (width, 1), 0)
    for x in range(width):
        edge = x if side == "left" else width - 1 - x
        fade = 1.0 if edge <= solid else max(0.0, 1 - (edge - solid) / float(max(1, reach - solid)))
        mask.putpixel((x, 0), int(255 * fade))
    image.paste(Image.new("RGB", size, SHADE_RGB), (0, 0), mask.resize(size))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=85)
    return out.getvalue()


def default_root():
    hdd = "/media/hdd"
    if os.path.ismount(hdd) and os.access(hdd, os.W_OK):
        return os.path.join(hdd, "kanasa", "cache"), 500 * 1024 * 1024
    return "/tmp/kanasa/cache", 50 * 1024 * 1024


class PosterCache:
    def __init__(self, root: str, cap_bytes: int, fetch=None, resize=None):
        self.root, self.cap = root, cap_bytes
        self.fetch, self.resize = fetch or _fetch, resize or _resize

    def path_for(self, url: str, size, rounded: int = 0, shade=None, play=False, progress=0) -> str:
        key = "%s|%dx%d" % (url, size[0], size[1]) + ("|r%d" % rounded if rounded else "") + \
            ("|s" + shade if shade else "") + ("|p" if play else "") + ("|g%d" % progress if progress else "")
        return os.path.join(self.root, hashlib.sha1(key.encode()).hexdigest() +
                            (".png" if rounded or play or progress else ".jpg"))

    def get(self, url: str, size, rounded: int = 0, shade=None, play=False, progress=0):
        if not url:
            return None
        path = self.path_for(url, size, rounded, shade, play, progress)
        if os.path.exists(path):
            return path
        try:
            raw = self.fetch(url)
            if play or progress:
                data = _card(raw, size, rounded, play, progress)
            elif rounded:
                data = _round(raw, size, rounded)
            elif shade:
                data = _shade(raw, size, shade)
            else:
                data = self.resize(raw, size)
            os.makedirs(self.root, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
            self._evict()
            return path if os.path.exists(path) else None
        except Exception:  # noqa: BLE001 - a poster never breaks the screen
            return None

    def _files(self) -> list:
        files = []
        try:
            names = os.listdir(self.root)
        except OSError:
            return files
        for name in names:
            full = os.path.join(self.root, name)
            if name.endswith((".jpg", ".png")) and os.path.isfile(full):
                stat = os.stat(full)
                files.append((stat.st_mtime, stat.st_size, full))
        return files

    def size_bytes(self) -> int:
        return sum(size for _, size, _ in self._files())

    def clear(self) -> None:
        for _, _, full in self._files():
            try:
                os.remove(full)
            except OSError:
                pass

    def _evict(self) -> None:
        files = self._files()
        total = sum(size for _, size, _ in files)
        for _, size, full in sorted(files):
            if total <= self.cap:
                break
            try:
                os.remove(full)
                total -= size
            except OSError:
                pass
