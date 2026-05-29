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
            return peer
        hosts.append(client)

    try:
        return await asyncio.wait_for(client.paired, timeout=timeout)
    except asyncio.TimeoutError:
        async with match_lock:
            if client in hosts:
                hosts.remove(client)
        return None


async def pipe(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    label: str,
    progress_interval: float,
    drain_warn_after: float,
    idle_warn_after: float,
) -> None:
    total = 0
    chunks = 0
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
                    total,
                    chunks,
                )
                continue

            if not data:
                logger.info(
                    "%s eof after %d bytes in %d chunks over %.1fs",
                    label,
                    total,
                    chunks,
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
                    total,
                )
                await drain_task

            total += len(data)
            chunks += 1
            now = time.monotonic()
            if now - last_progress >= progress_interval:
                logger.info(
                    "%s transferred %d bytes in %d chunks over %.1fs",
                    label,
                    total,
                    chunks,
                    now - started,
                )
                last_progress = now
    except asyncio.CancelledError:
        logger.warning("%s cancelled after %d bytes in %d chunks", label, total, chunks)
        raise
    except ConnectionError as exc:
        logger.warning(
            "%s connection error after %d bytes in %d chunks: %s",
            label,
            total,
            chunks,
            exc,
        )
    finally:
        destination.close()


async def bridge(
    a: Client,
    b: Client,
    progress_interval: float,
    drain_warn_after: float,
    idle_warn_after: float,
) -> None:
    session_id = next(session_ids)
    a.writer.write(b"OKAY")
    b.writer.write(b"OKAY")
    await asyncio.gather(a.writer.drain(), b.writer.drain())
    logger.info("session %d paired host=%s guest=%s", session_id, a.peer, b.peer)

    await asyncio.gather(
        pipe(
            a.reader,
            b.writer,
            f"session {session_id} host->guest",
            progress_interval,
            drain_warn_after,
            idle_warn_after,
        ),
        pipe(
            b.reader,
            a.writer,
            f"session {session_id} guest->host",
            progress_interval,
            drain_warn_after,
            idle_warn_after,
        ),
    )
    await asyncio.gather(
        close_writer(a.writer),
        close_writer(b.writer),
    )
    logger.info("session %d closed host=%s guest=%s", session_id, a.peer, b.peer)


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_secret: bytes,
    match_timeout: int,
    progress_interval: float,
    drain_warn_after: float,
    idle_warn_after: float,
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
        await bridge(client, peer_client, progress_interval, drain_warn_after, idle_warn_after)


async def main() -> None:
    setup_logging()
    expected_secret = read_secret()
    bind_host = os.environ.get("NETSLUG_RELAY_BIND_ADDR", "0.0.0.0")
    bind_port = int(os.environ.get("NETSLUG_RELAY_PORT", "10000"))
    match_timeout = int(os.environ.get("NETSLUG_RELAY_MATCH_TIMEOUT_SECS", "120"))
    progress_interval = float(os.environ.get("NETSLUG_RELAY_PROGRESS_INTERVAL_SECS", "10"))
    drain_warn_after = float(os.environ.get("NETSLUG_RELAY_DRAIN_WARN_SECS", "5"))
    idle_warn_after = float(os.environ.get("NETSLUG_RELAY_IDLE_WARN_SECS", "10"))

    server = await asyncio.start_server(
        lambda r, w: handle_client(
            r,
            w,
            expected_secret,
            match_timeout,
            progress_interval,
            drain_warn_after,
            idle_warn_after,
        ),
        bind_host,
        bind_port,
    )

    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logger.info(
        "netslug relay listening on %s match_timeout=%ss progress_interval=%.1fs drain_warn=%.1fs idle_warn=%.1fs",
        sockets,
        match_timeout,
        progress_interval,
        drain_warn_after,
        idle_warn_after,
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
