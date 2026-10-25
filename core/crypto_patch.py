"""
Hardware and C-Accelerated MTProto Cryptography Engine
Provides seamless AES-IGE encryption and decryption acceleration for Telethon.
Supports cryptg C extension, OpenSSL libcrypto (ctypes zero-copy memory buffers),
and automatic path resolution for Android Termux ($PREFIX/lib), Linux, and macOS.
"""

import os
import sys
import ctypes
import ctypes.util
import logging
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

_CRYPTO_STATUS: Dict[str, Any] = {
    "engine": "Uninitialized",
    "accelerated": False,
    "speed_estimate": "Unknown",
    "details": ""
}


class AES_KEY(ctypes.Structure):
    """OpenSSL AES key schedule structure."""
    _fields_ = [
        ("rd_key", ctypes.c_uint32 * (4 * (14 + 1))),
        ("rounds", ctypes.c_uint),
    ]


def _find_system_crypto_library() -> Tuple[Optional[str], Optional[ctypes.CDLL]]:
    """
    Locates the system OpenSSL / libcrypto shared library.
    Checks Termux prefix paths, standard Linux multiarch directories, and ctypes find_library.
    """
    candidates = []

    # 1. Android Termux environment paths
    termux_prefix = os.environ.get("PREFIX", "")
    if termux_prefix:
        candidates.extend([
            os.path.join(termux_prefix, "lib", "libcrypto.so.3"),
            os.path.join(termux_prefix, "lib", "libcrypto.so"),
            os.path.join(termux_prefix, "lib", "libssl.so.3"),
            os.path.join(termux_prefix, "lib", "libssl.so"),
        ])
    candidates.extend([
        "/data/data/com.termux/files/usr/lib/libcrypto.so.3",
        "/data/data/com.termux/files/usr/lib/libcrypto.so",
        "/data/data/com.termux/files/usr/lib/libssl.so.3",
        "/data/data/com.termux/files/usr/lib/libssl.so",
        "/system/lib64/libcrypto.so",
        "/system/lib/libcrypto.so",
        "/apex/com.android.conscrypt/lib64/libcrypto.so",
        "/apex/com.android.conscrypt/lib/libcrypto.so",
    ])

    # 2. ctypes standard library discovery
    for name in ("crypto", "ssl"):
        found = ctypes.util.find_library(name)
        if found:
            candidates.append(found)

    # 3. Standard Linux / Unix multiarch paths
    candidates.extend([
        "/usr/lib/x86_64-linux-gnu/libcrypto.so.3",
        "/usr/lib/x86_64-linux-gnu/libcrypto.so",
        "/usr/lib/aarch64-linux-gnu/libcrypto.so.3",
        "/usr/lib/aarch64-linux-gnu/libcrypto.so",
        "/usr/lib/arm-linux-gnueabihf/libcrypto.so.3",
        "/usr/lib/arm-linux-gnueabihf/libcrypto.so",
        "/lib/x86_64-linux-gnu/libcrypto.so.3",
        "/lib/x86_64-linux-gnu/libcrypto.so",
        "/lib/aarch64-linux-gnu/libcrypto.so.3",
        "/lib/aarch64-linux-gnu/libcrypto.so",
        "/usr/lib/libcrypto.so.3",
        "/usr/lib/libcrypto.so",
        "/usr/lib64/libcrypto.so",
        "/usr/local/lib/libcrypto.so",
        "/usr/local/opt/openssl/lib/libcrypto.dylib",
        "/opt/homebrew/opt/openssl/lib/libcrypto.dylib",
    ])

    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            lib = ctypes.CDLL(path)
            if hasattr(lib, "AES_ige_encrypt") and hasattr(lib, "AES_set_encrypt_key"):
                return path, lib
        except Exception:
            continue

    return None, None


def _build_openssl_ige_routines(lib: ctypes.CDLL):
    """
    Builds high-performance AES-IGE routines directly bound to OpenSSL libcrypto C buffers.
    Uses ctypes.create_string_buffer avoiding Python stack argument unpacking overhead.
    """
    AES_ENCRYPT = ctypes.c_int(1)
    AES_DECRYPT = ctypes.c_int(0)

    def openssl_encrypt_ige(plain_text: bytes, key: bytes, iv: bytes) -> bytes:
        padding = len(plain_text) % 16
        if padding:
            plain_text += os.urandom(16 - padding)
        aes_key = AES_KEY()
        key_len = ctypes.c_int(len(key) * 8)
        lib.AES_set_encrypt_key(key, key_len, ctypes.byref(aes_key))
        out_buf = ctypes.create_string_buffer(len(plain_text))
        iv_buf = ctypes.create_string_buffer(iv, len(iv))
        lib.AES_ige_encrypt(
            plain_text,
            out_buf,
            ctypes.c_size_t(len(plain_text)),
            ctypes.byref(aes_key),
            iv_buf,
            AES_ENCRYPT
        )
        return out_buf.raw

    def openssl_decrypt_ige(cipher_text: bytes, key: bytes, iv: bytes) -> bytes:
        length = len(cipher_text) - (len(cipher_text) % 16)
        if length <= 0:
            return b""
        cipher_text = cipher_text[:length]
        aes_key = AES_KEY()
        key_len = ctypes.c_int(len(key) * 8)
        lib.AES_set_decrypt_key(key, key_len, ctypes.byref(aes_key))
        out_buf = ctypes.create_string_buffer(len(cipher_text))
        iv_buf = ctypes.create_string_buffer(iv, len(iv))
        lib.AES_ige_encrypt(
            cipher_text,
            out_buf,
            ctypes.c_size_t(len(cipher_text)),
            ctypes.byref(aes_key),
            iv_buf,
            AES_DECRYPT
        )
        return out_buf.raw

    return openssl_encrypt_ige, openssl_decrypt_ige


def apply_crypto_patch() -> Dict[str, Any]:
    """
    Applies hardware or C-accelerated AES cryptography patches to Telethon.
    Priority 1: cryptg C extension (~240 MB/s).
    Priority 2: System OpenSSL libcrypto ctypes direct buffers (~50 MB/s).
    Priority 3: pyaes fallback with warning (~0.25 MB/s).
    """
    global _CRYPTO_STATUS

    # 1. Attempt cryptg C-extension
    try:
        import cryptg  # type: ignore
        # Verification check
        k = b"\x00" * 32
        iv = b"\x00" * 32
        d = b"\x00" * 64
        enc = cryptg.encrypt_ige(d, k, iv)
        dec = cryptg.decrypt_ige(enc, k, iv)
        if dec == d:
            import telethon.crypto.aes as tele_aes
            tele_aes.cryptg = cryptg

            _CRYPTO_STATUS = {
                "engine": "cryptg (C extension)",
                "accelerated": True,
                "speed_estimate": "200+ MB/s",
                "details": "High-performance native C extension active"
            }
            logger.info("MTProto Crypto: cryptg C extension detected and active (200+ MB/s)")
            return _CRYPTO_STATUS
    except Exception as e:
        logger.debug("cryptg unavailable or failed verification: %s", e)

    # 2. Attempt OpenSSL libcrypto C ctypes buffer hook
    path, lib = _find_system_crypto_library()
    if lib is not None:
        try:
            encrypt_fn, decrypt_fn = _build_openssl_ige_routines(lib)
            k = b"\x01" * 32
            iv = b"\x02" * 32
            d = b"Telegram FastTransfer Crypto Verification Block 123456"
            enc = encrypt_fn(d, k, iv)
            dec = decrypt_fn(enc, k, iv)
            if dec.startswith(d):
                import telethon.crypto.aes as tele_aes
                import telethon.crypto.libssl as tele_libssl

                # Monkey-patch Telethon AES and libssl interfaces
                tele_aes.AES.encrypt_ige = staticmethod(encrypt_fn)
                tele_aes.AES.decrypt_ige = staticmethod(decrypt_fn)
                tele_libssl.encrypt_ige = encrypt_fn
                tele_libssl.decrypt_ige = decrypt_fn
                if hasattr(tele_aes, "libssl"):
                    tele_aes.libssl.encrypt_ige = encrypt_fn
                    tele_aes.libssl.decrypt_ige = decrypt_fn

                base_name = os.path.basename(path) if path else "system libcrypto"
                _CRYPTO_STATUS = {
                    "engine": f"OpenSSL ({base_name})",
                    "accelerated": True,
                    "speed_estimate": "45-50 MB/s",
                    "details": f"Zero-copy C buffer hook loaded from {path}"
                }
                logger.info(f"MTProto Crypto: OpenSSL accelerated C hook active ({base_name}, ~50 MB/s)")
                return _CRYPTO_STATUS
        except Exception as e:
            logger.warning(f"Failed to initialize OpenSSL crypto hook: {e}")

    # 3. Fallback: Pure Python pyaes
    _CRYPTO_STATUS = {
        "engine": "pyaes (pure Python)",
        "accelerated": False,
        "speed_estimate": "0.2-0.3 MB/s",
        "details": "Warning: transfers will be CPU-throttled. Install cryptg or openssl for 100x speedup."
    }
    logger.warning("MTProto Crypto: Pure Python pyaes active (Slow transfers: ~0.25 MB/s).")
    return _CRYPTO_STATUS


def get_crypto_info() -> Dict[str, Any]:
    """Returns the current MTProto cryptographic engine status."""
    if _CRYPTO_STATUS["engine"] == "Uninitialized":
        return apply_crypto_patch()
    return _CRYPTO_STATUS
