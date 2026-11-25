"""085 FR-030/FR-033: the signed host bundle on the box. Pure (no Enigma2): fetch, verify, install, roll.

Same channel, key and zip as the Android app (contract 008 §2): the manifest signature is checked with the pinned
ed25519 key over the EXACT manifest bytes before anything is parsed; then format, downgrade, platforms.enigma2,
min_client, size and sha256 (before the zip is opened), and every zip entry (only under e2iweb/, no "..", no
absolute path, no backslash, no symlink, bounded size). The verifier lives here, never in the bundle.

Layout (FAT/exFAT safe: no symlinks):
    <root>/<v>/e2iweb/...           an installed bundle (+ <root>/<v>/manifest.json, the signed bytes)
    <root>/<v>.tmp/                 staging; renamed to <root>/<v> when complete
    <root>/current                  one line: the version in use, replaced atomically
Kept: the current bundle and the one before it. A broken download or a refused bundle keeps the current one.
Phase 0: nothing on screen uses this yet."""
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import zipfile

from . import updater

CLIENT_VERSION = 1                       # compared with platforms.enigma2.min_client (plan decision 9)
HDD_ROOT = "/media/hdd/kanasa/bundle"
FLASH_ROOT = "/home/root/kanasa/bundle"  # not /etc/enigma2: settings backups copy that folder
FLASH_STATE_CAP = 20 * 1024 * 1024       # FR-014: device HTTP cache + match store on flash
MAX_ZIP = 16 * 1024 * 1024               # published zips are ~0.32 MB
MAX_UNPACKED = 64 * 1024 * 1024
MAX_ENTRIES = 5000
PLATFORM = "enigma2"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Refused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- where ------------------------------------------------------------------------------------------------------
def choose_root(hdd_mounted: bool, hdd_writable: bool) -> str:
    return HDD_ROOT if hdd_mounted and hdd_writable else FLASH_ROOT


def default_root() -> str:
    from . import torrengine        # writable(): a real write probe (os.access lies on ro remounts)
    hdd = "/media/hdd"
    return choose_root(os.path.ismount(hdd), os.path.ismount(hdd) and torrengine.writable(hdd))


def state_cap_bytes(root: str):
    """The worker's device.setup(state_cap_bytes=...): 20 MB on flash, no cap on the disk."""
    return None if root.startswith("/media/") else FLASH_STATE_CAP


# --- verify --------------------------------------------------------------------------------------------------------
def verify_manifest(manifest_b64, sig_b64, public_key_pem=updater.PUBLIC_KEY_PEM):
    """(manifest dict, exact bytes) or raise Refused('signature'). Signature first, JSON after."""
    from cryptography.hazmat.primitives import serialization
    try:
        raw = base64.b64decode(manifest_b64, validate=True)
        signature = base64.b64decode(sig_b64, validate=True)
        serialization.load_pem_public_key(public_key_pem).verify(signature, raw)
    except Exception:  # noqa: BLE001 - every decode / signature failure is one refusal
        raise Refused("signature") from None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise Refused("format") from None
    if not isinstance(value, dict):
        raise Refused("format")
    return value, raw


def check_manifest(manifest: dict, installed=None, client_version=CLIENT_VERSION) -> dict:
    """The manifest's install facts, or raise Refused(<code>)."""
    if manifest.get("format") != 1:
        raise Refused("format")
    version = _int(manifest.get("version"))
    if version is None or version < 1:
        raise Refused("format")
    if installed is not None and version <= installed:
        raise Refused("not_newer")
    if manifest.get("zip") != "%d.zip" % version:
        raise Refused("format")
    size, sha256 = _int(manifest.get("size")), manifest.get("sha256")
    if size is None or not 0 < size <= MAX_ZIP or not isinstance(sha256, str) or not _HEX64.match(sha256):
        raise Refused("format")
    platforms = manifest.get("platforms")
    mine = platforms.get(PLATFORM) if isinstance(platforms, dict) else None
    if not isinstance(mine, dict):
        raise Refused("no_platform")
    hosts = mine.get("hosts")
    if not isinstance(hosts, list) or not all(isinstance(h, str) and h for h in hosts):
        raise Refused("no_platform")
    minimum = mine.get("min_client", 1)
    if _int(minimum) is None or minimum > client_version:
        raise Refused("client_too_old")
    domains = manifest.get("site_domains", {})
    if not isinstance(domains, dict):
        raise Refused("format")
    return {"version": version, "size": size, "sha256": sha256, "hosts": list(hosts),
            "site_domains": {k: v for k, v in domains.items() if isinstance(k, str) and isinstance(v, str)}}


def _safe_name(name: str) -> bool:
    if not name.startswith("e2iweb/") or "\\" in name or "\0" in name or name.startswith("/"):
        return False
    parts = name.rstrip("/").split("/")
    return all(part not in ("", ".", "..") for part in parts) and not re.match(r"^[A-Za-z]:", name)


def check_zip(path: str) -> list:
    """The zip's file entries, or raise Refused('zip'). Reads only the central directory."""
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (OSError, zipfile.BadZipFile, ValueError):
        raise Refused("zip") from None
    if not infos or len(infos) > MAX_ENTRIES:
        raise Refused("zip")
    total = 0
    for info in infos:
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)          # 0 when the zip records permissions only (bundle.py writes 0o644)
        if not _safe_name(info.filename) or (kind and kind not in (stat.S_IFREG, stat.S_IFDIR)):
            raise Refused("zip")
        total += info.file_size
        if total > MAX_UNPACKED:
            raise Refused("zip")
    return [info for info in infos if not info.is_dir()]


# --- install -----------------------------------------------------------------------------------------------------
def current(root: str):
    """(version, dir) of the bundle in use, or None."""
    try:
        with open(os.path.join(root, "current"), encoding="ascii") as handle:
            text = handle.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not re.fullmatch(r"[1-9]\d{0,8}", text):
        return None
    directory = os.path.join(root, text)
    if not os.path.isfile(os.path.join(directory, "e2iweb", "device", "__init__.py")):
        return None
    return int(text), directory


def installed_manifest(root: str):
    found = current(root)
    if found is None:
        return None
    try:
        with open(os.path.join(found[1], "manifest.json"), "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _write_atomic(path: str, data: bytes) -> None:
    part = path + ".part"
    with open(part, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, path)


def install(root: str, raw_manifest: bytes, facts: dict, zip_path: str) -> str:
    """Check size + sha256, then every entry, then extract to <v>.tmp, rename, switch `current`, prune.
    Returns the new bundle dir. On any failure the staging dir goes and `current` is untouched."""
    if not updater.check_file(zip_path, facts["size"], facts["sha256"]):
        raise Refused("checksum")
    entries = check_zip(zip_path)
    version = facts["version"]
    final, staging = os.path.join(root, str(version)), os.path.join(root, "%d.tmp" % version)
    shutil.rmtree(staging, ignore_errors=True)
    try:
        os.makedirs(staging)
        with zipfile.ZipFile(zip_path) as archive:
            for info in entries:
                target = os.path.join(staging, *info.filename.split("/"))
                if not os.path.realpath(target).startswith(os.path.realpath(staging) + os.sep):
                    raise Refused("zip")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out, 256 * 1024)
        with open(os.path.join(staging, "manifest.json"), "wb") as handle:
            handle.write(raw_manifest)
        if os.path.exists(final):
            shutil.rmtree(final, ignore_errors=True)       # a half-installed leftover of this version
        os.rename(staging, final)
    except Refused:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except (OSError, zipfile.BadZipFile, ValueError, EOFError):
        shutil.rmtree(staging, ignore_errors=True)
        raise Refused("disk") from None
    previous = current(root)
    _write_atomic(os.path.join(root, "current"), ("%d\n" % version).encode("ascii"))
    prune(root, keep={version, previous[0] if previous else version})
    return final


def prune(root: str, keep) -> list:
    removed = []
    try:
        names = os.listdir(root)
    except OSError:
        return removed
    for name in names:
        match = re.fullmatch(r"(\d+)(\.tmp|\.zip\.part)?", name)
        if match is None or (match.group(2) is None and int(match.group(1)) in keep):
            continue
        path = os.path.join(root, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.unlink(path)
            except OSError:
                continue
        removed.append(name)
    return removed


def update(root: str, fetch_manifest, download_zip, client_version=CLIENT_VERSION,
           public_key_pem=updater.PUBLIC_KEY_PEM) -> dict:
    """One check: fetch_manifest() -> {"manifest": b64, "sig": b64}; download_zip(version, path).
    -> {"state": "installed"|"current"|"refused"|"failed", "version", "code"}. Never raises."""
    found = current(root)
    have = found[0] if found else None
    try:
        answer = fetch_manifest()
        if not isinstance(answer, dict):
            raise Refused("format")
        manifest, raw = verify_manifest(answer.get("manifest"), answer.get("sig"), public_key_pem)
        try:
            facts = check_manifest(manifest, have, client_version)
        except Refused as refused:
            if refused.code == "not_newer":
                return {"state": "current", "version": have, "code": None}
            raise
        os.makedirs(root, exist_ok=True)
        part = os.path.join(root, "%d.zip.part" % facts["version"])
        try:
            download_zip(facts["version"], part)
            install(root, raw, facts, part)
        finally:
            try:
                os.unlink(part)
            except OSError:
                pass
        return {"state": "installed", "version": facts["version"], "code": None}
    except Refused as refused:
        return {"state": "refused", "version": have, "code": refused.code}
    except Exception as error:  # noqa: BLE001 - network / disk: keep what is installed
        return {"state": "failed", "version": have, "code": type(error).__name__}
