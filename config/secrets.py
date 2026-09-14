"""Machine-local secret storage for commercial configuration credentials."""

import ctypes
from ctypes import wintypes
import os
import tempfile
from pathlib import Path

from . import paths


SECRET_FILE_HEADER = b"BAS1\n"
SECRET_ID_PART_PATTERN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"


class SecretStoreError(RuntimeError):
    """Base error for secret storage failures."""


class SecretProtectionUnavailable(SecretStoreError):
    """Raised when the host cannot protect or unprotect secrets."""


class SecretProtectionError(SecretStoreError):
    """Raised when encryption or decryption fails."""


class SecretNotFound(SecretStoreError):
    """Raised when a referenced secret does not exist."""


class InvalidSecretId(SecretStoreError):
    """Raised when a secret id cannot be mapped safely below the secrets root."""


class WindowsMachineDPAPIProtector:
    """Protect secrets with Windows DPAPI in LocalMachine scope."""

    CRYPTPROTECT_LOCAL_MACHINE = 0x4

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def __init__(self):
        if os.name != "nt":
            raise SecretProtectionUnavailable("Windows DPAPI secret protection is only available on Windows.")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    def protect(self, plaintext):
        data = plaintext.encode("utf-8")
        in_blob, in_buffer = self._blob_from_bytes(data)
        out_blob = self.DATA_BLOB()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            self.CRYPTPROTECT_LOCAL_MACHINE,
            ctypes.byref(out_blob),
        )
        _ = in_buffer
        if not ok:
            raise SecretProtectionError("Could not protect secret with Windows DPAPI.")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            self._kernel32.LocalFree(out_blob.pbData)

    def unprotect(self, ciphertext):
        in_blob, in_buffer = self._blob_from_bytes(ciphertext)
        out_blob = self.DATA_BLOB()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        _ = in_buffer
        if not ok:
            raise SecretProtectionError("Could not decrypt protected secret.")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretProtectionError("Protected secret decrypted to invalid text.") from exc
        finally:
            self._kernel32.LocalFree(out_blob.pbData)

    def _blob_from_bytes(self, data):
        buffer = ctypes.create_string_buffer(data)
        return self.DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


class SecretStore:
    def __init__(self, secrets_dir=None, protector=None):
        self.secrets_dir = Path(secrets_dir) if secrets_dir is not None else paths.get_secrets_dir()
        self.protector = protector or WindowsMachineDPAPIProtector()

    def set_secret(self, secret_id, value):
        secret_path = self._path_for_secret(secret_id)
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        protected = self.protector.protect(str(value))
        fd, temp_name = tempfile.mkstemp(prefix=secret_path.name + ".", suffix=".tmp", dir=str(secret_path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(SECRET_FILE_HEADER)
                handle.write(protected)
            if self._read_secret_file(temp_path) != str(value):
                raise SecretProtectionError("Protected secret verification failed.")
            os.replace(str(temp_path), str(secret_path))
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise
        return secret_id

    def get_secret(self, secret_id):
        secret_path = self._path_for_secret(secret_id)
        if not secret_path.is_file():
            raise SecretNotFound("Referenced secret is missing: " + secret_id)
        return self._read_secret_file(secret_path)

    def delete_secret(self, secret_id):
        secret_path = self._path_for_secret(secret_id)
        try:
            secret_path.unlink()
        except FileNotFoundError:
            return False
        return True

    def secret_exists(self, secret_id):
        return self._path_for_secret(secret_id).is_file()

    def _read_secret_file(self, secret_path):
        data = secret_path.read_bytes()
        if not data.startswith(SECRET_FILE_HEADER):
            raise SecretProtectionError("Protected secret file is corrupt.")
        try:
            return self.protector.unprotect(data[len(SECRET_FILE_HEADER):])
        except SecretStoreError:
            raise
        except Exception as exc:
            raise SecretProtectionError("Could not decrypt protected secret.") from exc

    def _path_for_secret(self, secret_id):
        parts = validate_secret_id(secret_id).split("/")
        relative = Path(*parts[:-1], parts[-1] + ".secret")
        candidate = (self.secrets_dir / relative).resolve()
        root = self.secrets_dir.resolve()
        if candidate != root and root not in candidate.parents:
            raise InvalidSecretId("Secret id resolves outside the secrets directory.")
        return candidate


def validate_secret_id(secret_id):
    value = str(secret_id or "")
    parts = value.split("/")
    if not value or value.startswith("/") or value.endswith("/") or "\\" in value or ":" in value:
        raise InvalidSecretId("Secret id is invalid.")
    for part in parts:
        if not part or part in (".", ".."):
            raise InvalidSecretId("Secret id is invalid.")
        if any(character not in SECRET_ID_PART_PATTERN for character in part):
            raise InvalidSecretId("Secret id is invalid.")
    return value


def erpnext_api_key_secret_id():
    return "erpnext/api_key"


def erpnext_api_secret_secret_id():
    return "erpnext/api_secret"


def device_password_secret_id(device_id):
    from .schema import validate_device_id

    return "devices/" + validate_device_id(device_id) + "/password"


def create_secret_store(paths_module=paths, protector=None):
    return SecretStore(paths_module.get_secrets_dir(), protector=protector)


def set_secret(secret_id, value, paths_module=paths, protector=None):
    return create_secret_store(paths_module=paths_module, protector=protector).set_secret(secret_id, value)


def get_secret(secret_id, paths_module=paths, protector=None):
    return create_secret_store(paths_module=paths_module, protector=protector).get_secret(secret_id)


def delete_secret(secret_id, paths_module=paths, protector=None):
    return create_secret_store(paths_module=paths_module, protector=protector).delete_secret(secret_id)


def secret_exists(secret_id, paths_module=paths, protector=None):
    return create_secret_store(paths_module=paths_module, protector=protector).secret_exists(secret_id)
