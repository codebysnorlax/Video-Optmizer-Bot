"""
High-Speed Parallel MTProto File Transfer Engine
Accelerates Telegram downloads and uploads using multi-connection chunk streaming.
Bypasses single-chunk sequential latency bottlenecks to achieve multi-megabyte/s throughput.
Seamlessly falls back to Telethon native methods on edge-case connectivity issues.
"""

import os
import sys
import math
import time
import inspect
import asyncio
import logging
import hashlib
from pathlib import Path
from typing import (
    Optional,
    Callable,
    Union,
    Tuple,
    List,
    AsyncGenerator,
    BinaryIO
)

from telethon import TelegramClient, helpers, utils
from telethon.crypto import AuthKey
from telethon.network import MTProtoSender
from telethon.tl.alltlobjects import LAYER
from telethon.tl.functions import InvokeWithLayerRequest
from telethon.tl.functions.auth import (
    ExportAuthorizationRequest,
    ImportAuthorizationRequest,
)
from telethon.tl.functions.upload import (
    GetFileRequest,
    SaveBigFilePartRequest,
    SaveFilePartRequest,
)
from telethon.tl.types import (
    Document,
    InputDocumentFileLocation,
    InputFile,
    InputFileBig,
    InputFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    TypeInputFile,
)

TypeLocation = Union[
    Document,
    InputDocumentFileLocation,
    InputPeerPhotoFileLocation,
    InputFileLocation,
    InputPhotoFileLocation,
]

logger = logging.getLogger(__name__)

# Maximum concurrent connections per single transfer
MAX_PARALLEL_CONNECTIONS = 4


class DownloadSender:
    """Manages downloading chunks sequentially on a dedicated MTProto sender connection."""

    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file: TypeLocation,
        offset: int,
        limit: int,
        stride: int,
        count: int,
    ) -> None:
        self.client = client
        self.sender = sender
        self.file = file
        self.offset = offset
        self.limit = limit
        self.stride = stride
        self.remaining = count

    async def next(self) -> Optional[bytes]:
        if self.remaining <= 0:
            return None
        req = GetFileRequest(self.file, offset=self.offset, limit=self.limit)
        result = await self.client._call(self.sender, req)
        self.remaining -= 1
        self.offset += self.stride
        return result.bytes

    async def disconnect(self) -> None:
        try:
            await self.sender.disconnect()
        except Exception as e:
            logger.debug("Error disconnecting download sender: %s", e)


class UploadSender:
    """Manages uploading chunks in parallel pipelines on a dedicated MTProto sender connection."""

    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file_id: int,
        part_count: int,
        big: bool,
        index: int,
        stride: int,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.client = client
        self.sender = sender
        self.file_id = file_id
        self.part_count = part_count
        self.big = big
        self.file_part = index
        self.stride = stride
        self.loop = loop
        self.previous: Optional[asyncio.Task] = None

    async def next(self, data: bytes) -> None:
        if self.previous:
            await self.previous
        self.previous = self.loop.create_task(self._next(data, self.file_part))
        self.file_part += self.stride

    async def _next(self, data: bytes, part_index: int) -> None:
        if self.big:
            req = SaveBigFilePartRequest(self.file_id, part_index, self.part_count, data)
        else:
            req = SaveFilePartRequest(self.file_id, part_index, data)
        await self.client._call(self.sender, req)

    async def disconnect(self) -> None:
        if self.previous:
            await self.previous
        try:
            await self.sender.disconnect()
        except Exception as e:
            logger.debug("Error disconnecting upload sender: %s", e)


class ParallelTransferrer:
    """Orchestrates multi-connection parallel MTProto download and upload pipelines."""

    def __init__(self, client: TelegramClient, dc_id: Optional[int] = None) -> None:
        self.client = client
        self.loop = client.loop
        self.dc_id = dc_id or client.session.dc_id
        self.auth_key = (
            None
            if dc_id and client.session.dc_id != dc_id
            else client.session.auth_key
        )
        self.senders: List[Union[DownloadSender, UploadSender]] = []
        self.upload_ticker = 0

    async def _create_sender(self) -> MTProtoSender:
        dc = await self.client._get_dc(self.dc_id)
        sender = MTProtoSender(self.auth_key, loggers=self.client._log)
        await sender.connect(
            self.client._connection(
                dc.ip_address,
                dc.port,
                dc.id,
                loggers=self.client._log,
                proxy=self.client._proxy,
            )
        )
        if not self.auth_key:
            auth = await self.client(ExportAuthorizationRequest(self.dc_id))
            init_req = getattr(self.client, "_init_request", None)
            if init_req:
                init_req.query = ImportAuthorizationRequest(
                    id=auth.id, bytes=auth.bytes
                )
                req = InvokeWithLayerRequest(LAYER, init_req)
            else:
                req = ImportAuthorizationRequest(id=auth.id, bytes=auth.bytes)
            await sender.send(req)
            self.auth_key = sender.auth_key
        return sender

    async def init_download(
        self,
        file: TypeLocation,
        file_size: int,
        connections: int
    ) -> Tuple[int, int]:
        part_size = utils.get_appropriated_part_size(file_size) * 1024
        part_count = math.ceil(file_size / part_size)
        connections = max(1, min(connections, part_count))

        minimum, remainder = divmod(part_count, connections)

        def get_sender_count() -> int:
            nonlocal remainder
            if remainder > 0:
                remainder -= 1
                return minimum + 1
            return minimum

        # First sender handles cross-DC authorization if required
        first_sender = await self._create_download_sender(
            file, 0, part_size, connections * part_size, get_sender_count()
        )
        other_senders = await asyncio.gather(*[
            self._create_download_sender(
                file, i, part_size, connections * part_size, get_sender_count()
            )
            for i in range(1, connections)
        ])
        self.senders = [first_sender, *other_senders]
        return part_size, part_count

    async def _create_download_sender(
        self,
        file: TypeLocation,
        index: int,
        part_size: int,
        stride: int,
        part_count: int,
    ) -> DownloadSender:
        return DownloadSender(
            self.client,
            await self._create_sender(),
            file,
            index * part_size,
            part_size,
            stride,
            part_count,
        )

    async def init_upload(
        self,
        file_id: int,
        file_size: int,
        connections: int
    ) -> Tuple[int, int, bool]:
        part_size = utils.get_appropriated_part_size(file_size) * 1024
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * 1024 * 1024
        connections = max(1, min(connections, part_count))

        first_sender = await self._create_upload_sender(
            file_id, part_count, is_large, 0, connections
        )
        other_senders = await asyncio.gather(*[
            self._create_upload_sender(
                file_id, part_count, is_large, i, connections
            )
            for i in range(1, connections)
        ])
        self.senders = [first_sender, *other_senders]
        return part_size, part_count, is_large

    async def _create_upload_sender(
        self,
        file_id: int,
        part_count: int,
        big: bool,
        index: int,
        stride: int,
    ) -> UploadSender:
        return UploadSender(
            self.client,
            await self._create_sender(),
            file_id,
            part_count,
            big,
            index,
            stride,
            loop=self.loop,
        )

    async def upload_part(self, data: bytes) -> None:
        sender = self.senders[self.upload_ticker]
        await sender.next(data)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)

    async def download_stream(self, part_count: int) -> AsyncGenerator[bytes, None]:
        part = 0
        while part < part_count:
            active_senders = [s for s in self.senders if isinstance(s, DownloadSender) and s.remaining > 0]
            if not active_senders:
                break
            tasks = [self.loop.create_task(s.next()) for s in active_senders]
            for task in tasks:
                chunk = await task
                if chunk is not None:
                    yield chunk
                    part += 1

    async def cleanup(self) -> None:
        if self.senders:
            await asyncio.gather(*[s.disconnect() for s in self.senders], return_exceptions=True)
            self.senders = []


def _calculate_optimal_workers(file_size: int, max_limit: int = MAX_PARALLEL_CONNECTIONS) -> int:
    """Calculates ideal connection count based on file size to balance speed and FloodWait safety."""
    if file_size <= 2 * 1024 * 1024:
        return 1
    if file_size <= 15 * 1024 * 1024:
        return 2
    if file_size <= 50 * 1024 * 1024:
        return min(3, max_limit)
    return min(4, max_limit)


async def fast_download(
    client: TelegramClient,
    location_or_message,
    out_path: Union[str, Path],
    progress_callback: Optional[Callable] = None,
    max_workers: int = MAX_PARALLEL_CONNECTIONS
) -> Path:
    """
    Downloads media using multi-connection parallel MTProto chunk streaming.
    Falls back cleanly to standard client.download_media on any network or protocol error.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        file_info = utils._get_file_info(location_or_message)
    except Exception as e:
        logger.debug("Could not resolve file info for fast download: %s. Using default download.", e)
        file_info = None

    if not file_info or not file_info.location or not file_info.size:
        # Standard fallback for non-document types
        await client.download_media(
            location_or_message,
            file=str(out_path),
            progress_callback=progress_callback
        )
        return out_path

    dc_id = file_info.dc_id
    input_location = file_info.location
    total_size = file_info.size
    workers = _calculate_optimal_workers(total_size, max_workers)

    # For tiny files, standard single connection is faster without extra TCP handshakes
    if workers <= 1:
        await client.download_media(
            location_or_message,
            file=str(out_path),
            progress_callback=progress_callback
        )
        return out_path

    transferrer = ParallelTransferrer(client, dc_id=dc_id)
    downloaded_bytes = 0
    try:
        part_size, part_count = await transferrer.init_download(
            file=input_location,
            file_size=total_size,
            connections=workers
        )

        with open(out_path, "wb") as f:
            async for chunk in transferrer.download_stream(part_count):
                f.write(chunk)
                downloaded_bytes += len(chunk)
                if progress_callback:
                    res = progress_callback(downloaded_bytes, total_size)
                    if inspect.isawaitable(res):
                        await res

        return out_path
    except asyncio.CancelledError:
        # User requested task cancellation
        if out_path.exists():
            try:
                out_path.unlink()
            except Exception:
                pass
        raise
    except Exception as e:
        logger.warning("Parallel download encountered issue (%s). Falling back to standard download...", e)
        await client.download_media(
            location_or_message,
            file=str(out_path),
            progress_callback=progress_callback
        )
        return out_path
    finally:
        await transferrer.cleanup()


async def fast_upload(
    client: TelegramClient,
    file_path: Union[str, Path],
    filename: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
    max_workers: int = MAX_PARALLEL_CONNECTIONS
) -> TypeInputFile:
    """
    Uploads a local file using multi-connection parallel MTProto chunk streaming.
    Returns an InputFile or InputFileBig ready to be passed directly to client.send_file.
    Falls back cleanly to standard client.upload_file on any network or protocol error.
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found for upload: {file_path}")

    file_size = file_path.stat().st_size
    upload_name = filename or file_path.name
    workers = _calculate_optimal_workers(file_size, max_workers)

    if workers <= 1:
        return await client.upload_file(
            file=str(file_path),
            file_name=upload_name,
            progress_callback=progress_callback
        )

    file_id = helpers.generate_random_long()
    transferrer = ParallelTransferrer(client)
    hash_md5 = hashlib.md5()
    uploaded_bytes = 0

    try:
        part_size, part_count, is_large = await transferrer.init_upload(
            file_id=file_id,
            file_size=file_size,
            connections=workers
        )

        buffer = bytearray()
        chunk_read_size = min(part_size, 512 * 1024)

        with open(file_path, "rb") as f:
            while True:
                data = f.read(chunk_read_size)
                if not data:
                    break

                if not is_large:
                    hash_md5.update(data)

                if len(buffer) == 0 and len(data) == part_size:
                    await transferrer.upload_part(data)
                    uploaded_bytes += len(data)
                    if progress_callback:
                        res = progress_callback(uploaded_bytes, file_size)
                        if inspect.isawaitable(res):
                            await res
                    continue

                buffer.extend(data)
                while len(buffer) >= part_size:
                    part_bytes = bytes(buffer[:part_size])
                    buffer = buffer[part_size:]
                    await transferrer.upload_part(part_bytes)
                    uploaded_bytes += len(part_bytes)
                    if progress_callback:
                        res = progress_callback(uploaded_bytes, file_size)
                        if inspect.isawaitable(res):
                            await res

            if len(buffer) > 0:
                final_bytes = bytes(buffer)
                await transferrer.upload_part(final_bytes)
                uploaded_bytes += len(final_bytes)
                if progress_callback:
                    res = progress_callback(uploaded_bytes, file_size)
                    if inspect.isawaitable(res):
                        await res

        # Ensure all queued worker upload tasks finish before completing
        await transferrer.cleanup()

        if is_large:
            return InputFileBig(id=file_id, parts=part_count, name=upload_name)
        else:
            return InputFile(
                id=file_id,
                parts=part_count,
                name=upload_name,
                md5_checksum=hash_md5.hexdigest()
            )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Parallel upload encountered issue (%s). Falling back to standard upload...", e)
        return await client.upload_file(
            file=str(file_path),
            file_name=upload_name,
            progress_callback=progress_callback
        )
    finally:
        await transferrer.cleanup()
