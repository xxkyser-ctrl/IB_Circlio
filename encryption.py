"""Local key management and authenticated encryption for IB Circlio data."""

import base64
import ctypes
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from file_permissions import restrict_to_current_user


FORMAT_VERSION = 1
METADATA_NAME = "encryption.json"
DPAPI_KEY_NAME = "master-key.dpapi"
TOKEN_NAME = "server-token.enc"
AVATAR_MAGIC = b"IBCA\x01"
TOKEN_MAGIC = b"IBCT\x01"
KEY_BYTES = 32
NONCE_BYTES = 12
_MASTER_KEY_CACHE = {}


class EncryptionError(ValueError):
    """Raised when encrypted data or key material cannot be safely opened."""


class MissingKeyError(EncryptionError):
    """The metadata exists, but no usable key was provided."""


def metadata_path(data_dir):
    return Path(data_dir) / METADATA_NAME


def clear_cached_master_key(data_dir):
    _MASTER_KEY_CACHE.pop(str(Path(data_dir).resolve()), None)


def load_metadata(data_dir):
    path = metadata_path(data_dir)
    if not path.exists():
        return None
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EncryptionError("The encryption metadata is unreadable.") from error
    if (
        not isinstance(metadata, dict)
        or metadata.get("encrypted") is not True
        or metadata.get("format_version") != FORMAT_VERSION
        or metadata.get("key_mode") not in ("dpapi", "passphrase")
    ):
        raise EncryptionError("The encryption metadata is invalid or unsupported.")
    return metadata


def _atomic_write(path, data, *, secret=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())
        if secret:
            restrict_to_current_user(temporary_path)
        os.replace(temporary_path, path)
        if secret:
            restrict_to_current_user(path)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def _dpapi(data, *, protect):
    if os.name != "nt":
        raise EncryptionError("Windows DPAPI is only available on Windows.")

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = DataBlob(len(data), source_buffer)
    destination = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(DataBlob),
        ]
        function.restype = ctypes.c_int
        succeeded = function(
            ctypes.byref(source), "IB Circlio master key", None, None, None,
            0, ctypes.byref(destination),
        )
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(DataBlob),
        ]
        function.restype = ctypes.c_int
        succeeded = function(
            ctypes.byref(source), None, None, None, None, 0,
            ctypes.byref(destination),
        )
    if not succeeded:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(destination.pbData)


def derive_subkey(master_key, purpose):
    if len(master_key) != KEY_BYTES:
        raise EncryptionError("The master key has an invalid length.")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=None,
        info=f"IB Circlio v1 {purpose}".encode("ascii"),
    ).derive(master_key)


def _recovery_wrap(master_key, recovery_key):
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(NONCE_BYTES)
    wrapping_key = hashlib.scrypt(
        recovery_key.encode("ascii"),
        salt=salt,
        n=2**15,
        r=8,
        p=1,
        maxmem=128 * 1024 * 1024,
        dklen=KEY_BYTES,
    )
    ciphertext = AESGCM(wrapping_key).encrypt(
        nonce, master_key, b"IB Circlio recovery key v1"
    )
    return {
        "kdf": "scrypt-n32768-r8-p1",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "wrapped_key": base64.b64encode(ciphertext).decode("ascii"),
    }


def _passphrase_wrap(master_key, passphrase):
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise EncryptionError("Use a passphrase with at least 12 characters.")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(NONCE_BYTES)
    wrapping_key = hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=2**15, r=8, p=1,
        maxmem=128 * 1024 * 1024, dklen=KEY_BYTES,
    )
    ciphertext = AESGCM(wrapping_key).encrypt(
        nonce, master_key, b"IB Circlio passphrase key v1"
    )
    return {
        "kdf": "scrypt-n32768-r8-p1",
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "wrapped_key": base64.b64encode(ciphertext).decode("ascii"),
    }


def _unwrap(envelope, secret, purpose):
    try:
        if envelope.get("kdf") != "scrypt-n32768-r8-p1":
            raise ValueError("unsupported key derivation")
        salt = base64.b64decode(envelope["salt"], validate=True)
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["wrapped_key"], validate=True)
        wrapping_key = hashlib.scrypt(
            secret.encode("ascii" if purpose == "recovery" else "utf-8"),
            salt=salt,
            n=2**15,
            r=8,
            p=1,
            maxmem=128 * 1024 * 1024,
            dklen=KEY_BYTES,
        )
        master_key = AESGCM(wrapping_key).decrypt(
            nonce, ciphertext, f"IB Circlio {purpose} key v1".encode("ascii")
        )
    except (InvalidTag, KeyError, TypeError, ValueError, UnicodeError) as error:
        raise EncryptionError(
            "The recovery key or passphrase is incorrect, or its key envelope is damaged."
        ) from error
    if len(master_key) != KEY_BYTES:
        raise EncryptionError("The decrypted master key has an invalid length.")
    return master_key


def initialize_encryption(data_dir, *, mode="dpapi", passphrase=None):
    data_dir = Path(data_dir)
    if load_metadata(data_dir):
        raise EncryptionError("Encryption is already configured for this data folder.")
    if mode not in ("dpapi", "passphrase"):
        raise EncryptionError("Unsupported key mode.")
    master_key = secrets.token_bytes(KEY_BYTES)
    recovery_key = base64.b32encode(secrets.token_bytes(KEY_BYTES)).decode("ascii").rstrip("=")
    metadata = {
        "encrypted": True,
        "format_version": FORMAT_VERSION,
        "key_mode": mode,
        "recovery": _recovery_wrap(master_key, recovery_key),
    }
    if mode == "dpapi":
        protected_key = _dpapi(master_key, protect=True)
        _atomic_write(data_dir / DPAPI_KEY_NAME, protected_key, secret=True)
    else:
        metadata["passphrase"] = _passphrase_wrap(master_key, passphrase)
    _atomic_write(
        metadata_path(data_dir),
        (json.dumps(metadata, indent=2) + "\n").encode("utf-8"),
    )
    _MASTER_KEY_CACHE[str(data_dir.resolve())] = master_key
    return master_key, recovery_key


def load_master_key(data_dir, *, passphrase=None, recovery_key=None):
    cache_key = str(Path(data_dir).resolve())
    metadata = load_metadata(data_dir)
    if metadata is None:
        return None
    key_override = os.environ.get("IB_CIRCLIO_KEY")
    if key_override is not None:
        if len(key_override) != 64 or not all(
            character in "0123456789abcdefABCDEF" for character in key_override
        ):
            raise EncryptionError("IB_CIRCLIO_KEY must contain exactly 64 hexadecimal characters.")
        master_key = bytes.fromhex(key_override)
        _MASTER_KEY_CACHE[cache_key] = master_key
        return master_key

    if passphrase is None and recovery_key is None:
        cached_key = _MASTER_KEY_CACHE.get(cache_key)
        if cached_key is not None:
            return cached_key
    passphrase = passphrase or os.environ.get("IB_CIRCLIO_PASSPHRASE")
    recovery_key = recovery_key or os.environ.get("IB_CIRCLIO_RECOVERY_KEY")
    if recovery_key:
        master_key = _unwrap(metadata["recovery"], recovery_key, "recovery")
        _MASTER_KEY_CACHE[cache_key] = master_key
        return master_key
    if metadata["key_mode"] == "dpapi":
        path = Path(data_dir) / DPAPI_KEY_NAME
        try:
            master_key = _dpapi(path.read_bytes(), protect=False)
        except FileNotFoundError as error:
            raise MissingKeyError(
                "The Windows-protected key is missing. Restore it from a backup or use the recovery key."
            ) from error
        if len(master_key) != KEY_BYTES:
            raise EncryptionError("The decrypted master key has an invalid length.")
        _MASTER_KEY_CACHE[cache_key] = master_key
        return master_key
    if not passphrase:
        raise MissingKeyError("Enter the passphrase or recovery key to open this encrypted data.")
    master_key = _unwrap(metadata["passphrase"], passphrase, "passphrase")
    _MASTER_KEY_CACHE[cache_key] = master_key
    return master_key


def is_encrypted(data_dir):
    return load_metadata(data_dir) is not None


def encrypt_avatar(data, master_key):
    nonce = secrets.token_bytes(NONCE_BYTES)
    return AVATAR_MAGIC + nonce + AESGCM(derive_subkey(master_key, "avatars")).encrypt(
        nonce, data, AVATAR_MAGIC
    )


def decrypt_avatar(data, master_key):
    if not data.startswith(AVATAR_MAGIC) or len(data) < len(AVATAR_MAGIC) + NONCE_BYTES:
        raise EncryptionError("The avatar has an invalid encrypted-file header.")
    nonce_start = len(AVATAR_MAGIC)
    nonce = data[nonce_start : nonce_start + NONCE_BYTES]
    try:
        return AESGCM(derive_subkey(master_key, "avatars")).decrypt(
            nonce, data[nonce_start + NONCE_BYTES :], AVATAR_MAGIC
        )
    except InvalidTag as error:
        raise EncryptionError("The encrypted avatar failed authentication.") from error


def read_avatar(path, data_dir):
    data = Path(path).read_bytes()
    if data.startswith(AVATAR_MAGIC):
        master_key = load_master_key(data_dir)
        if master_key is None:
            raise MissingKeyError("The encrypted avatar has no usable data key.")
        return decrypt_avatar(data, master_key)
    if is_encrypted(data_dir):
        raise EncryptionError("An avatar in an encrypted data folder is unexpectedly plaintext.")
    return data


def encrypt_token(token, master_key):
    nonce = secrets.token_bytes(NONCE_BYTES)
    ciphertext = AESGCM(derive_subkey(master_key, "token")).encrypt(
        nonce, token.encode("utf-8"), TOKEN_MAGIC
    )
    return TOKEN_MAGIC + nonce + ciphertext


def decrypt_token(data, master_key):
    if not data.startswith(TOKEN_MAGIC) or len(data) < len(TOKEN_MAGIC) + NONCE_BYTES:
        raise EncryptionError("The protected token file has an invalid format.")
    nonce_start = len(TOKEN_MAGIC)
    try:
        return AESGCM(derive_subkey(master_key, "token")).decrypt(
            data[nonce_start : nonce_start + NONCE_BYTES],
            data[nonce_start + NONCE_BYTES :],
            TOKEN_MAGIC,
        ).decode("utf-8")
    except (InvalidTag, UnicodeError) as error:
        raise EncryptionError("The protected token file failed authentication.") from error


def write_token(data_dir, token):
    data_dir = Path(data_dir)
    master_key = load_master_key(data_dir)
    if master_key is None:
        path = data_dir / "server-token.txt"
        _atomic_write(path, (token + "\n").encode("utf-8"), secret=True)
        return path
    path = data_dir / TOKEN_NAME
    _atomic_write(path, encrypt_token(token, master_key), secret=True)
    legacy_token = data_dir / "server-token.txt"
    if legacy_token.exists():
        legacy_token.unlink()
    return path


def read_token(data_dir):
    data_dir = Path(data_dir)
    encrypted_token = data_dir / TOKEN_NAME
    if encrypted_token.exists():
        master_key = load_master_key(data_dir)
        if master_key is None:
            raise EncryptionError("The protected token has no encryption metadata.")
        return decrypt_token(encrypted_token.read_bytes(), master_key)
    legacy_token = data_dir / "server-token.txt"
    if legacy_token.exists():
        return legacy_token.read_text(encoding="utf-8").strip()
    return ""
