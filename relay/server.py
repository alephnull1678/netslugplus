#!/usr/bin/env python3
import asyncio
import logging
import os
import secrets
import struct
from pathlib import Path

MAGIC = b"NSR1"
HELLO_SIZE = 72
ROLE_HOST = 1
ROLE_GUEST = 2

ROOT = Path(__file__).resolve().parents[1]
SECRET_FILE = Path(os.environ.get("RELAY_SECRET_FILE", ROOT / "secrets" / "relay_secret.txt"))
HOST = os.environ.get("RELAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("RELAY_PORT", "10000"))

pending = {}


def load_or_create_secret():
    if SECRET_FILE.exists():
        value = SECRET_FILE.read_text(encoding="ascii").strip()
        if value:
            return value

    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(16)
    SECRET_FILE.write_text(value + "\n", encoding="ascii")
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return value


async def read_hello(reader):
    data = await asyncio.wait_for(reader.readexactly(HELLO_SIZE), timeout=10)
    magic = data[:4]
    role = struct.unpack(">I", data[4:8])[0]
    secret = data[8:].split(b"\0", 1)[0].decode("ascii", "ignore")
    if magic != MAGIC or role not in (ROLE_HOST, ROLE_GUEST):
        raise ValueError("bad relay hello")
    return role, secret


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def relay_pair(host_reader, host_writer, guest_reader, guest_writer):
    host_peer = host_writer.get_extra_info("peername")
    guest_peer = guest_writer.get_extra_info("peername")
    logging.info("paired host=%s guest=%s", host_peer, guest_peer)
    await asyncio.gather(
        pipe(host_reader, guest_writer),
        pipe(guest_reader, host_writer),
    )
    logging.info("pair closed host=%s guest=%s", host_peer, guest_peer)


async def handle_client(reader, writer):
    peer = writer.get_extra_info("peername")
    try:
        role, client_secret = await read_hello(reader)
        if client_secret != relay_secret:
            logging.warning("rejected %s: bad secret", peer)
            writer.close()
            await writer.wait_closed()
            return

        role_name = "host" if role == ROLE_HOST else "guest"
        logging.info("%s connected from %s", role_name, peer)

        entry = pending.setdefault(client_secret, {})
        opposite = ROLE_GUEST if role == ROLE_HOST else ROLE_HOST
        if opposite in entry:
            other_reader, other_writer = entry.pop(opposite)
            entry.pop(role, None)
            if not entry:
                pending.pop(client_secret, None)
            if role == ROLE_HOST:
                await relay_pair(reader, writer, other_reader, other_writer)
            else:
                await relay_pair(other_reader, other_writer, reader, writer)
        else:
            old = entry.pop(role, None)
            if old:
                old[1].close()
            entry[role] = (reader, writer)
    except Exception as exc:
        logging.warning("closed %s: %s", peer, exc)
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def main():
    server = await asyncio.start_server(handle_client, HOST, PORT)
    logging.info("relay listening on %s:%d", HOST, PORT)
    logging.info("relay secret: %s", relay_secret)
    logging.info("relay secret file: %s", SECRET_FILE)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    relay_secret = load_or_create_secret()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("relay stopped")