#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import signal
import time
from dataclasses import dataclass


MAGIC = b"NSR1"
HEADER_SIZE = 8
ROLE_HOST = 1
ROLE_GUEST = 2
MAX_SECRET_LEN = 128


@dataclass
class Client:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    role: int
    peer: str
    paired: asyncio.Future["Client"]


@dataclass
class PipeStats:
    total: int = 0
    chunks: int = 0
    eof: bool = False


waiting_hosts: list[Client] = []
waiting_guests: list[Client] = []
match_lock = asyncio.Lock()
session_ids = itertools.count(1)
logger = logging.getLogger("netslug-relay")


def setup_logging() -> None:
    level_name = os.environ.get("NETSLUG_RELAY_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def read_secret() -> bytes:
    path = os.environ.get("NETSLUG_RELAY_SECRET_FILE", "relay_secret.txt")
    with open(path, "rb") as secret_file:
        secret = secret_file.readline().strip()
    if not secret:
        raise RuntimeError(f"{path} is empty")
    if len(secret) > MAX_SECRET_LEN:
        raise RuntimeError(f"{path} is longer than {MAX_SECRET_LEN} bytes")
    return secret


def peer_name(writer: asyncio.StreamWriter) -> str:
    peer = writer.get_extra_info("peername")
    if not peer:
        return "unknown"
    return f"{peer[0]}:{peer[1]}"


def role_name(role: int) -> str:
    return "host" if role == ROLE_HOST else "guest"


def bytes_preview(data: bytes, limit: int) -> str:
    if limit <= 0:
        return ""
    preview = data[:limit]
    suffix = " ..." if len(data) > limit else ""
    return f"{preview.hex(' ')}{suffix}"


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass


async def read_hello(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_secret: bytes,
) -> int | None:
    peer = peer_name(writer)
    try:
        header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=15)
        if header[:4] != MAGIC:
            logger.warning("rejected %s: bad magic %r", peer, header[:4])
            return None

        role = header[4]
        secret_len = (header[6] << 8) | header[7]
        if role not in (ROLE_HOST, ROLE_GUEST) or secret_len > MAX_SECRET_LEN:
            logger.warning(
                "rejected %s: invalid role=%d secret_len=%d",
                peer,
                role,
                secret_len,
            )
            return None

        secret = await asyncio.wait_for(reader.readexactly(secret_len), timeout=15)
        if secret != expected_secret:
            writer.write(b"FAIL")
            await writer.drain()
            logger.warning("rejected %s: wrong secret", peer)
            return None

        logger.info(
            "accepted hello peer=%s role=%s secret_len=%d",
            peer,
            role_name(role),
            secret_len,
        )
        return role
    except asyncio.TimeoutError:
        logger.warning("rejected %s: timed out during hello", peer)
        return None
    except asyncio.IncompleteReadError as exc:
        logger.warning(
            "rejected %s: disconnected during hello after %d bytes",
            peer,
            len(exc.partial),
        )
        return None
    except ConnectionError as exc:
        logger.warning("rejected %s: connection error during hello: %s", peer, exc)
        return None


async def wait_for_peer(client: Client, timeout: int) -> Client | None:
    hosts = waiting_hosts if client.role == ROLE_HOST else waiting_guests
    guests = waiting_guests if client.role == ROLE_HOST else waiting_hosts

    async with match_lock:
        if guests:
            peer = guests.pop(0)
            if not peer.paired.done():
                peer.paired.set_result(client)
            logger.info(
                "matched waiting %s %s with %s %s queue_hosts=%d queue_guests=%d",
                role_name(peer.role),
                peer.peer,
                role_name(client.role),
                client.peer,
                len(waiting_hosts),
                len(waiting_guests),
            )
            return peer
        hosts.append(client)
        logger.info(
            "queued %s %s queue_hosts=%d queue_guests=%d",
            role_name(client.role),
            client.peer,
            len(waiting_hosts),
            len(waiting_guests),
        )

    try:
        return await asyncio.wait_for(client.paired, timeout=timeout)
    except asyncio.TimeoutError:
        async with match_lock:
            if client in hosts:
                hosts.remove(client)
            logger.info(
                "removed timed-out %s %s queue_hosts=%d queue_guests=%d",
                role_name(client.role),
                client.peer,
                len(waiting_hosts),
                len(waiting_guests),
            )
        return None


async def pipe(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    label: str,
    progress_interval: float,
    drain_warn_after: float,
    idle_warn_after: float,
    chunk_log_limit: int,
    chunk_preview_bytes: int,
    stats: PipeStats,
) -> None:
    started = time.monotonic()
    last_progress = started
    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    source.read(64 * 1024),
                    timeout=idle_warn_after,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "%s idle for more than %.1fs after %d bytes in %d chunks",
                    label,
                    idle_warn_after,
                    stats.total,
                    stats.chunks,
                )
                continue

            if not data:
                stats.eof = True
                logger.info(
                    "%s eof after %d bytes in %d chunks over %.1fs",
                    label,
                    stats.total,
                    stats.chunks,
                    time.monotonic() - started,
                )
                break
            destination.write(data)
            drain_task = asyncio.create_task(destination.drain())
            try:
                await asyncio.wait_for(
                    asyncio.shield(drain_task),
                    timeout=drain_warn_after,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "%s drain blocked for more than %.1fs after %d bytes",
                    label,
                    drain_warn_after,
                    stats.total,
                )
                await drain_task

            stats.total += len(data)
            stats.chunks += 1
            if stats.chunks <= chunk_log_limit:
                logger.info(
                    "%s chunk=%d len=%d total=%d first_bytes=%s",
                    label,
                    stats.chunks,
                    len(data),
                    stats.total,
                    bytes_preview(data, chunk_preview_bytes),
                )
            now = time.monotonic()
            if now - last_progress >= progress_interval:
                logger.info(
                    "%s transferred %d bytes in %d chunks over %.1fs",
                    label,
                    stats.total,
                    stats.chunks,
                    now - started,
                )
                last_progress = now
    except asyncio.CancelledError:
        logger.warning(
            "%s cancelled after %d bytes in %d chunks",
            label,
            stats.total,
            stats.chunks,
        )
        raise
    except ConnectionError as exc:
        logger.warning(
            "%s connection error after %d bytes in %d chunks: %s",
            label,
            stats.total,
            stats.chunks,
            exc,
        )
    finally:
        destination.close()


async def close_if_startup_stalled(
    session_id: int,
    host_to_guest: PipeStats,
    guest_to_host: PipeStats,
    host_writer: asyncio.StreamWriter,
    guest_writer: asyncio.StreamWriter,
    startup_stall_after: float,
    startup_min_bytes: int,
) -> None:
    if startup_stall_after <= 0:
        return

    await asyncio.sleep(startup_stall_after)
    if host_to_guest.eof or guest_to_host.eof:
        return
    if host_to_guest.total <= startup_min_bytes and guest_to_host.total <= startup_min_bytes:
        logger.warning(
            "session %d startup stalled for %.1fs: host->guest=%d bytes/%d chunks guest->host=%d bytes/%d chunks; closing both peers",
            session_id,
            startup_stall_after,
            host_to_guest.total,
            host_to_guest.chunks,
            guest_to_host.total,
            guest_to_host.chunks,
        )
        host_writer.close()
        guest_writer.close()


async def bridge(
    a: Client,
    b: Client,
    progress_interval: float,
    drain_warn_after: float,
    idle_warn_after: float,
    chunk_log_limit: int,
    chunk_preview_bytes: int,
    startup_stall_after: float,
    startup_min_bytes: int,
) -> None:
    session_id = next(session_ids)
    host_to_guest = PipeStats()
    guest_to_host = PipeStats()

    logger.info("session %d sending OKAY to host=%s guest=%s", session_id, a.peer, b.peer)
    a.writer.write(b"OKAY")
    b.writer.write(b"OKAY")
    await asyncio.gather(a.writer.drain(), b.writer.drain())
    logger.info("session %d paired host=%s guest=%s", session_id, a.peer, b.peer)

    stall_task = asyncio.create_task(
        close_if_startup_stalled(
            session_id,
            host_to_guest,
            guest_to_host,
            a.writer,
            b.writer,
            startup_stall_after,
            startup_min_bytes,
        )
    )
    await asyncio.gather(
        pipe(
            a.reader,
            b.writer,
            f"session {session_id} host->guest",
            progress_interval,
            drain_warn_after,
            idle_warn_after,
            chunk_log_limit,
            chunk_preview_bytes,
            host_to_guest,
        ),
        pipe(
            b.reader,
            a.writer,
            f"session {session_id} guest->host",
            progress_interval,
            drain_warn_after,
            idle_warn_after,
            chunk_log_limit,
            chunk_preview_bytes,
            guest_to_host,
        ),
    )
    stall_task.cancel()
    await asyncio.gather(stall_task, return_exceptions=True)
    await asyncio.gather(
        close_writer(a.writer),
        close_writer(b.writer),
    )
    logger.info(
        "session %d closed host=%s guest=%s totals host->guest=%d/%d guest->host=%d/%d",
        session_id,
        a.peer,
        b.peer,
        host_to_guest.total,
        host_to_guest.chunks,
        guest_to_host.total,
        guest_to_host.chunks,
    )


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_secret: bytes,
    match_timeout: int,
    progress_interval: float,
    drain_warn_after: float,
    idle_warn_after: float,
    chunk_log_limit: int,
    chunk_preview_bytes: int,
    startup_stall_after: float,
    startup_min_bytes: int,
) -> None:
    peer = peer_name(writer)
    role = await read_hello(reader, writer, expected_secret)
    if role is None:
        await close_writer(writer)
        return

    loop = asyncio.get_running_loop()
    client = Client(reader, writer, role, peer, loop.create_future())
    role_name = "host" if role == ROLE_HOST else "guest"
    logger.info("accepted %s %s", role_name, peer)

    peer_client = await wait_for_peer(client, match_timeout)
    if peer_client is None:
        writer.write(b"FAIL")
        await writer.drain()
        await close_writer(writer)
        logger.warning("timed out waiting for peer: %s %s", role_name, peer)
        return

    if role == ROLE_HOST:
        await bridge(
            client,
            peer_client,
            progress_interval,
            drain_warn_after,
            idle_warn_after,
            chunk_log_limit,
            chunk_preview_bytes,
            startup_stall_after,
            startup_min_bytes,
        )


async def main() -> None:
    setup_logging()
    expected_secret = read_secret()
    bind_host = os.environ.get("NETSLUG_RELAY_BIND_ADDR", "0.0.0.0")
    bind_port = int(os.environ.get("NETSLUG_RELAY_PORT", "10000"))
    match_timeout = int(os.environ.get("NETSLUG_RELAY_MATCH_TIMEOUT_SECS", "120"))
    progress_interval = float(os.environ.get("NETSLUG_RELAY_PROGRESS_INTERVAL_SECS", "10"))
    drain_warn_after = float(os.environ.get("NETSLUG_RELAY_DRAIN_WARN_SECS", "5"))
    idle_warn_after = float(os.environ.get("NETSLUG_RELAY_IDLE_WARN_SECS", "10"))
    chunk_log_limit = int(os.environ.get("NETSLUG_RELAY_CHUNK_LOG_LIMIT", "8"))
    chunk_preview_bytes = int(os.environ.get("NETSLUG_RELAY_CHUNK_PREVIEW_BYTES", "64"))
    startup_stall_after = float(os.environ.get("NETSLUG_RELAY_STARTUP_STALL_SECS", "30"))
    startup_min_bytes = int(os.environ.get("NETSLUG_RELAY_STARTUP_MIN_BYTES", "4"))

    server = await asyncio.start_server(
        lambda r, w: handle_client(
            r,
            w,
            expected_secret,
            match_timeout,
            progress_interval,
            drain_warn_after,
            idle_warn_after,
            chunk_log_limit,
            chunk_preview_bytes,
            startup_stall_after,
            startup_min_bytes,
        ),
        bind_host,
        bind_port,
    )

    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logger.info(
        "netslug relay listening on %s match_timeout=%ss progress_interval=%.1fs drain_warn=%.1fs idle_warn=%.1fs chunk_log_limit=%d chunk_preview_bytes=%d startup_stall=%.1fs startup_min_bytes=%d",
        sockets,
        match_timeout,
        progress_interval,
        drain_warn_after,
        idle_warn_after,
        chunk_log_limit,
        chunk_preview_bytes,
        startup_stall_after,
        startup_min_bytes,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with server:
        await stop.wait()


if __name__ == "__main__":
    asyncio.run(main())
