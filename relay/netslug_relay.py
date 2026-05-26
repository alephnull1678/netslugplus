#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import os
import signal
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
    try:
        header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=15)
        if header[:4] != MAGIC:
            return None

        role = header[4]
        secret_len = (header[6] << 8) | header[7]
        if role not in (ROLE_HOST, ROLE_GUEST) or secret_len > MAX_SECRET_LEN:
            return None

        secret = await asyncio.wait_for(reader.readexactly(secret_len), timeout=15)
        if secret != expected_secret:
            writer.write(b"FAIL")
            await writer.drain()
            return None

        return role
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
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
) -> None:
    try:
        while True:
            data = await source.read(64 * 1024)
            if not data:
                break
            destination.write(data)
            await destination.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        destination.close()


async def bridge(a: Client, b: Client) -> None:
    a.writer.write(b"OKAY")
    b.writer.write(b"OKAY")
    await asyncio.gather(a.writer.drain(), b.writer.drain())
    print(f"paired {a.peer} <-> {b.peer}", flush=True)

    await asyncio.gather(
        pipe(a.reader, b.writer),
        pipe(b.reader, a.writer),
    )
    await asyncio.gather(
        close_writer(a.writer),
        close_writer(b.writer),
    )
    print(f"closed {a.peer} <-> {b.peer}", flush=True)


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_secret: bytes,
    match_timeout: int,
) -> None:
    peer = peer_name(writer)
    role = await read_hello(reader, writer, expected_secret)
    if role is None:
        await close_writer(writer)
        print(f"rejected {peer}", flush=True)
        return

    loop = asyncio.get_running_loop()
    client = Client(reader, writer, role, peer, loop.create_future())
    role_name = "host" if role == ROLE_HOST else "guest"
    print(f"accepted {role_name} {peer}", flush=True)

    peer_client = await wait_for_peer(client, match_timeout)
    if peer_client is None:
        writer.write(b"FAIL")
        await writer.drain()
        await close_writer(writer)
        print(f"timed out {role_name} {peer}", flush=True)
        return

    if role == ROLE_HOST:
        await bridge(client, peer_client)


async def main() -> None:
    expected_secret = read_secret()
    bind_host = os.environ.get("NETSLUG_RELAY_BIND_ADDR", "0.0.0.0")
    bind_port = int(os.environ.get("NETSLUG_RELAY_PORT", "10000"))
    match_timeout = int(os.environ.get("NETSLUG_RELAY_MATCH_TIMEOUT_SECS", "120"))

    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, expected_secret, match_timeout),
        bind_host,
        bind_port,
    )

    sockets = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"netslug relay listening on {sockets}", flush=True)

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
