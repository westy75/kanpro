"""Pure release verification and update decisions (no Enigma2 imports)."""
import base64
import hashlib
import json
import os
import re

from cryptography.hazmat.primitives import serialization


PUBLIC_KEY_PEM = b"""-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAXY6Jy7BCjFIptC8QbPRwfLA8LUAN0E7CvjeXH42PbBg=
-----END PUBLIC KEY-----
"""
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(value):
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(remote, local):
    remote_value, local_value = parse_version(remote), parse_version(local)
    return remote_value is not None and local_value is not None and remote_value > local_value


def verify(manifest_b64, sig_b64, public_key_pem=PUBLIC_KEY_PEM):
    """Return the signed JSON object, or None for every decode/signature/shape failure."""
    try:
        manifest = base64.b64decode(manifest_b64, validate=True)
        signature = base64.b64decode(sig_b64, validate=True)
        key = serialization.load_pem_public_key(public_key_pem)
        key.verify(signature, manifest)
        value = json.loads(manifest.decode("utf-8"))
        return value if isinstance(value, dict) else None
    except Exception:  # noqa: BLE001 - untrusted server response must fail closed
        return None


def must_check(now, checked_at, forced_pending, local_version, interval):
    """Ask the server again? Every `interval` seconds, and on EVERY open while a mandatory release seen
    earlier is still newer than this plugin (box 2026-11-21: Back on the mandatory popup, reopen = no popup
    for 6 hours)."""
    if forced_pending and is_newer(forced_pending, local_version):
        return True
    return now - (checked_at or 0) >= interval


def decide(manifest, local_version, skipped=None):
    if not isinstance(manifest, dict):
        return "none"
    remote = manifest.get("version")
    if not is_newer(remote, local_version):
        return "none"
    minimum = manifest.get("min_version")
    forced = manifest.get("mandatory") is True
    if minimum is not None:
        minimum_value, local_value = parse_version(minimum), parse_version(local_version)
        if minimum_value is None or local_value is None:
            return "none"
        forced = forced or local_value < minimum_value
    if forced:
        return "force"
    return "none" if remote == skipped else "offer"


def check_file(path, size, sha256):
    if isinstance(size, bool) or not isinstance(size, int) or size < 0 \
            or not isinstance(sha256, str) or re.fullmatch(r"[0-9a-f]{64}", sha256) is None:
        return False
    try:
        if os.path.getsize(path) != size:
            return False
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == sha256
    except OSError:
        return False


def notes_for(manifest, lang):
    """The release notes lines for lang (English when that language has none), at most 6."""
    notes = manifest.get("notes") if isinstance(manifest, dict) else None
    if not isinstance(notes, dict):
        return []
    lines = notes.get(lang) if isinstance(notes.get(lang), list) and notes.get(lang) else notes.get("en")
    return [line.strip() for line in (lines or []) if isinstance(line, str) and line.strip()][:6] \
        if isinstance(lines, list) else []
