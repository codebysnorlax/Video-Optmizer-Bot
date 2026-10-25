"""
Unit Tests for MTProto Crypto Acceleration and Fast Transfer Engine
"""

import os
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from core.crypto_patch import (
    apply_crypto_patch,
    get_crypto_info,
    _find_system_crypto_library,
    _build_openssl_ige_routines
)
from core.fast_transfer import (
    _calculate_optimal_workers,
    fast_download,
    fast_upload
)
from telethon.tl.types import InputFileBig, InputFile


def test_crypto_patch_application():
    """Verifies crypto patch detects an accelerated engine and correctly encrypts/decrypts."""
    status = apply_crypto_patch()
    assert status["accelerated"] is True
    assert "Uninitialized" not in status["engine"]

    info = get_crypto_info()
    assert info["accelerated"] is True

    # Test AES IGE round-trip via Telethon AES
    import telethon.crypto.aes as tele_aes
    key = b"\x11" * 32
    iv = b"\x22" * 32
    data = b"Testing high performance AES IGE encryption and decryption payload." * 50

    encrypted = tele_aes.AES.encrypt_ige(data, key, iv)
    assert encrypted != data
    decrypted = tele_aes.AES.decrypt_ige(encrypted, key, iv)
    assert decrypted.startswith(data)


def test_openssl_ctypes_ige_routines():
    """Directly tests OpenSSL C buffer IGE implementation."""
    path, lib = _find_system_crypto_library()
    if lib is not None:
        enc_fn, dec_fn = _build_openssl_ige_routines(lib)
        key = os.urandom(32)
        iv = os.urandom(32)
        raw_data = os.urandom(1024 * 32)  # 32KB

        ciphertext = enc_fn(raw_data, key, iv)
        assert len(ciphertext) >= len(raw_data)
        plaintext = dec_fn(ciphertext, key, iv)
        assert plaintext.startswith(raw_data)


def test_optimal_worker_calculation():
    """Verifies that connection counts scale safely with file size."""
    # Tiny file <= 2MB -> 1 connection (no connection churn)
    assert _calculate_optimal_workers(500 * 1024) == 1
    assert _calculate_optimal_workers(2 * 1024 * 1024) == 1

    # Medium file 15MB -> 2 connections
    assert _calculate_optimal_workers(15 * 1024 * 1024) == 2

    # Moderate file 40MB -> 3 connections
    assert _calculate_optimal_workers(40 * 1024 * 1024) == 3

    # Large file 150MB -> capped at 4 connections
    assert _calculate_optimal_workers(150 * 1024 * 1024) == 4
    assert _calculate_optimal_workers(500 * 1024 * 1024, max_limit=6) == 4


@pytest.mark.asyncio
async def test_fast_download_fallback_on_unsupported_media(tmp_path):
    """Verifies that fast_download gracefully delegates to client.download_media when needed."""
    out_file = tmp_path / "fallback_video.mp4"
    mock_client = AsyncMock()

    async def fake_download(msg, file, progress_callback=None):
        with open(file, "wb") as f:
            f.write(b"simulated media content")

    mock_client.download_media.side_effect = fake_download

    # Pass an arbitrary message mock without Document location
    dummy_message = MagicMock()
    dummy_message.media = None

    result = await fast_download(
        client=mock_client,
        location_or_message=dummy_message,
        out_path=out_file
    )

    assert result == out_file
    assert out_file.exists()
    assert out_file.read_bytes() == b"simulated media content"
    assert mock_client.download_media.called


@pytest.mark.asyncio
async def test_fast_upload_small_file_delegation(tmp_path):
    """Verifies that small files (<=2MB) delegate directly to standard client.upload_file without multi-worker overhead."""
    small_file = tmp_path / "tiny.mp4"
    small_file.write_bytes(b"A" * 1024)  # 1 KB

    mock_client = AsyncMock()
    expected_input_file = InputFile(id=9999, parts=1, name="tiny.mp4", md5_checksum="abc")
    mock_client.upload_file.return_value = expected_input_file

    result = await fast_upload(
        client=mock_client,
        file_path=small_file
    )

    assert result == expected_input_file
    mock_client.upload_file.assert_called_once()


@pytest.mark.asyncio
async def test_fast_download_cancellation_propagation(tmp_path):
    """Verifies that user task cancellation cleanly aborts fast_download."""
    out_file = tmp_path / "cancelled_video.mp4"
    mock_client = AsyncMock()

    async def cancelling_download(msg, file, progress_callback=None):
        if progress_callback:
            await progress_callback(100, 1000)
        raise asyncio.CancelledError("User stopped download")

    mock_client.download_media.side_effect = cancelling_download

    with pytest.raises(asyncio.CancelledError):
        await fast_download(
            client=mock_client,
            location_or_message=MagicMock(),
            out_path=out_file
        )
