#!/usr/bin/env python3
import asyncio
import itertools
import logging
import os
import secrets
import struct
import time
from pathlib import Path

MAGIC = b"NSR1"
PAIR_READY = b"NSOK"
HELLO_SIZE = 72
HELLO_TIMEOUT = float(os.environ.get("RELAY_HELLO_TIMEOUT", "10"))
ROLE_HOST = 1
ROLE_GUEST = 2
ROLE_NAMES = {
    ROLE_HOST: "host",
    ROLE_GUEST: "guest",
}

ROOT = Path(__file__).resolve().parents[1]
SECRET_FILE = Path(os.environ.get("RELAY_SECRET_FILE", ROOT / "secrets" / "relay_secret.txt"))
HOST = os.environ.get("RELAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("RELAY_PORT", "10000"))

pending = {}
connection_ids = itertools.count(1)


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


def preview_bytes(data, limit=32):
    if not data:
        return "<empty>"
    suffix = "" if len(data) <= limit else "..."
    return data[:limit].hex(" ") + suffix


def pending_summary():
    summary = []
    for secret_key, roles in pending.items():
        role_names = [ROLE_NAMES.get(role, str(role)) for role in roles]
        summary.append(f"secret_len={len(secret_key)} roles={role_names}")
    return "; ".join(summary) if summary else "empty"


async def read_hello(reader, conn_id, peer):
    data = bytearray()
    deadline = time.monotonic() + HELLO_TIMEOUT
    logging.info("conn=%s peer=%s waiting for %d-byte hello timeout=%.1fs", conn_id, peer, HELLO_SIZE, HELLO_TIMEOUT)

    while len(data) < HELLO_SIZE:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logging.warning(
                "conn=%s peer=%s hello timeout bytes=%d/%d preview=%s",
                conn_id,
                peer,
                len(data),
                HELLO_SIZE,
                preview_bytes(data),
            )
            raise TimeoutError(f"hello timeout after {len(data)}/{HELLO_SIZE} bytes")

        try:
            chunk = await asyncio.wait_for(reader.read(HELLO_SIZE - len(data)), timeout=remaining)
        except asyncio.TimeoutError:
            logging.warning(
                "conn=%s peer=%s hello wait_for timeout bytes=%d/%d preview=%s",
                conn_id,
                peer,
                len(data),
                HELLO_SIZE,
                preview_bytes(data),
            )
            raise TimeoutError(f"hello timeout after {len(data)}/{HELLO_SIZE} bytes")

        if not chunk:
            logging.warning(
                "conn=%s peer=%s closed during hello bytes=%d/%d preview=%s",
                conn_id,
                peer,
                len(data),
                HELLO_SIZE,
                preview_bytes(data),
            )
            raise EOFError(f"closed during hello after {len(data)}/{HELLO_SIZE} bytes")

        data.extend(chunk)
        logging.info(
            "conn=%s peer=%s hello chunk=%d total=%d/%d preview=%s",
            conn_id,
            peer,
            len(chunk),
            len(data),
            HELLO_SIZE,
            preview_bytes(data),
        )

    data = bytes(data)
    magic = data[:4]
    role = struct.unpack(">I", data[4:8])[0]
    raw_secret = data[8:]
    secret = raw_secret.split(b"\0", 1)[0].decode("ascii", "ignore")

    logging.info(
        "conn=%s peer=%s hello parsed magic=%r role=%s secret_len=%d raw_secret_preview=%s",
        conn_id,
        peer,
        magic,
        ROLE_NAMES.get(role, f"unknown:{role}"),
        len(secret),
        preview_bytes(raw_secret),
    )

    if magic != MAGIC:
        raise ValueError(f"bad relay magic {magic!r}")
    if role not in ROLE_NAMES:
        raise ValueError(f"bad relay role {role}")
    return role, secret


async def pipe(reader, writer, label):
    total = 0
    peer = writer.get_extra_info("peername")
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                logging.info("pipe=%s eof after bytes=%d", label, total)
                break
            total += len(data)
            logging.debug("pipe=%s forwarding chunk=%d total=%d", label, len(data), total)
            writer.write(data)
            await writer.drain()
    except asyncio.CancelledError:
        logging.info("pipe=%s cancelled after bytes=%d", label, total)
        raise
    except ConnectionError as exc:
        logging.warning("pipe=%s connection error after bytes=%d peer=%s error=%r", label, total, peer, exc)
    finally:
        logging.info("pipe=%s closing writer peer=%s total=%d", label, peer, total)
        writer.close()


async def relay_pair(host_reader, host_writer, guest_reader, guest_writer, host_id, guest_id):
    host_peer = host_writer.get_extra_info("peername")
    guest_peer = guest_writer.get_extra_info("peername")
    logging.info("pair host_conn=%s host=%s guest_conn=%s guest=%s", host_id, host_peer, guest_id, guest_peer)

    host_writer.write(PAIR_READY)
    guest_writer.write(PAIR_READY)
    await asyncio.gather(host_writer.drain(), guest_writer.drain())
    logging.info("pair host_conn=%s guest_conn=%s sent pair-ready ack=%r", host_id, guest_id, PAIR_READY)

    await asyncio.gather(
        pipe(host_reader, guest_writer, f"host:{host_id}->guest:{guest_id}"),
        pipe(guest_reader, host_writer, f"guest:{guest_id}->host:{host_id}"),
    )
    logging.info("pair closed host_conn=%s host=%s guest_conn=%s guest=%s", host_id, host_peer, guest_id, guest_peer)


async def close_writer(writer, conn_id, reason):
    peer = writer.get_extra_info("peername")
    logging.info("conn=%s peer=%s closing reason=%s", conn_id, peer, reason)
    writer.close()
    try:
        await writer.wait_closed()
    except ConnectionError as exc:
        logging.warning("conn=%s peer=%s wait_closed error=%r", conn_id, peer, exc)


async def handle_client(reader, writer):
    conn_id = next(connection_ids)
    peer = writer.get_extra_info("peername")
    sockname = writer.get_extra_info("sockname")
    logging.info("conn=%s accepted peer=%s local=%s pending=%s", conn_id, peer, sockname, pending_summary())

    paired = False
    try:
        role, client_secret = await read_hello(reader, conn_id, peer)
        if client_secret != relay_secret:
            logging.warning(
                "conn=%s peer=%s rejected bad secret role=%s client_secret_len=%d expected_len=%d",
                conn_id,
                peer,
                ROLE_NAMES.get(role, role),
                len(client_secret),
                len(relay_secret),
            )
            await close_writer(writer, conn_id, "bad secret")
            return

        role_name = ROLE_NAMES[role]
        logging.info("conn=%s peer=%s accepted role=%s", conn_id, peer, role_name)

        entry = pending.setdefault(client_secret, {})
        opposite = ROLE_GUEST if role == ROLE_HOST else ROLE_HOST
        logging.info(
            "conn=%s role=%s before pairing entry_roles=%s pending=%s",
            conn_id,
            role_name,
            [ROLE_NAMES.get(item, item) for item in entry],
            pending_summary(),
        )

        if opposite in entry:
            other_reader, other_writer, other_id = entry.pop(opposite)
            entry.pop(role, None)
            if not entry:
                pending.pop(client_secret, None)
            paired = True
            logging.info("conn=%s found opposite role=%s other_conn=%s pending=%s", conn_id, ROLE_NAMES[opposite], other_id, pending_summary())
            if role == ROLE_HOST:
                await relay_pair(reader, writer, other_reader, other_writer, conn_id, other_id)
            else:
                await relay_pair(other_reader, other_writer, reader, writer, other_id, conn_id)
        else:
            old = entry.pop(role, None)
            if old:
                _old_reader, old_writer, old_id = old
                logging.warning("conn=%s replacing old pending %s conn=%s", conn_id, role_name, old_id)
                await close_writer(old_writer, old_id, "replaced by newer same role")
            entry[role] = (reader, writer, conn_id)
            logging.info("conn=%s stored pending role=%s pending=%s", conn_id, role_name, pending_summary())
    except Exception as exc:
        logging.warning("conn=%s peer=%s closed/error %r", conn_id, peer, exc)
        await close_writer(writer, conn_id, f"exception {exc!r}")
    finally:
        if not paired:
            for secret_key, roles in list(pending.items()):
                for role_key, value in list(roles.items()):
                    if len(value) >= 3 and value[2] == conn_id and writer.is_closing():
                        roles.pop(role_key, None)
                        logging.info("conn=%s removed closed pending role=%s", conn_id, ROLE_NAMES.get(role_key, role_key))
                if not roles:
                    pending.pop(secret_key, None)
        logging.info("conn=%s done pending=%s", conn_id, pending_summary())


async def main():
    server = await asyncio.start_server(handle_client, HOST, PORT)
    logging.info("relay listening on %s:%d", HOST, PORT)
    logging.info("relay secret: %s", relay_secret)
    logging.info("relay secret length: %d", len(relay_secret))
    logging.info("relay secret file: %s", SECRET_FILE)
    logging.info("relay hello timeout: %.1fs", HELLO_TIMEOUT)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    relay_secret = load_or_create_secret()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("relay stopped")