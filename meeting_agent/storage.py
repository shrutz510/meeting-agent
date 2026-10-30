"""Encrypted local storage for meetings.

Each meeting is stored as a single file ``<id>.json.enc`` (or ``<id>.json``
when encryption is disabled). Encryption uses Fernet (AES-128-CBC + HMAC-SHA256)
with a key derived from a passphrase via scrypt. The passphrase is never
written to disk; supply it with ``MEETING_AGENT_PASSPHRASE`` or interactively.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

SALT_FILE = "salt.bin"
CHECK_FILE = "key-check.enc"
CHECK_PLAINTEXT = b"meeting-agent-key-check"


class WrongPassphrase(Exception):
    pass


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def secure_delete(path: Path) -> None:
    """Best-effort overwrite then unlink. (SSDs/APFS may keep copies; full-disk
    encryption is the real protection, this just avoids leaving plain files.)"""
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(secrets.token_bytes(min(size, 64 * 1024 * 1024)))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


class MeetingStore:
    def __init__(self, root: Path, encrypt: bool = True, passphrase: str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.encrypt = encrypt
        self._fernet: Fernet | None = None
        if encrypt:
            self._fernet = self._init_key(passphrase)

    def _init_key(self, passphrase: str | None) -> Fernet:
        salt_path = self.root / SALT_FILE
        check_path = self.root / CHECK_FILE
        first_time = not salt_path.exists()
        if passphrase is None:
            passphrase = os.environ.get("MEETING_AGENT_PASSPHRASE")
        if passphrase is None:
            passphrase = getpass.getpass("Store passphrase: ")
            if first_time and getpass.getpass("Confirm passphrase: ") != passphrase:
                raise WrongPassphrase("Passphrases do not match")
        if not passphrase:
            raise WrongPassphrase("Empty passphrase")
        if first_time:
            _write_private(salt_path, secrets.token_bytes(16))
        fernet = Fernet(_derive_key(passphrase, salt_path.read_bytes()))
        if check_path.exists():
            try:
                fernet.decrypt(check_path.read_bytes())
            except InvalidToken as e:
                raise WrongPassphrase("Wrong passphrase for this store") from e
        else:
            _write_private(check_path, fernet.encrypt(CHECK_PLAINTEXT))
        return fernet

    def _path(self, meeting_id: str) -> Path:
        if not meeting_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"Invalid meeting id: {meeting_id!r}")
        return self.root / (f"{meeting_id}.json.enc" if self.encrypt else f"{meeting_id}.json")

    @staticmethod
    def new_id(title: str = "") -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")[:40]
        slug = "-".join(p for p in slug.split("-") if p)
        return f"{stamp}-{slug}" if slug else stamp

    def save(self, meeting_id: str, record: dict[str, Any]) -> Path:
        raw = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        if self._fernet:
            raw = self._fernet.encrypt(raw)
        path = self._path(meeting_id)
        _write_private(path, raw)
        return path

    def load(self, meeting_id: str) -> dict[str, Any]:
        raw = self._path(meeting_id).read_bytes()
        if self._fernet:
            raw = self._fernet.decrypt(raw)
        return json.loads(raw)

    def list(self) -> list[str]:
        suffix = ".json.enc" if self.encrypt else ".json"
        return sorted(p.name[: -len(suffix)] for p in self.root.glob(f"*{suffix}")
                      if p.name != CHECK_FILE)

    def encrypt_file(self, src: Path, dest: Path) -> None:
        data = src.read_bytes()
        _write_private(dest, self._fernet.encrypt(data) if self._fernet else data)

    def delete(self, meeting_id: str) -> None:
        secure_delete(self._path(meeting_id))
        for audio in self.root.glob(f"{meeting_id}.audio*"):
            secure_delete(audio)
