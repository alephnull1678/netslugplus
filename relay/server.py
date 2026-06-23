#!/usr/bin/env python3
import asyncio
import itertools
import logging
import os
import secrets
from pathlib import Path

PAIR_READY = b"NSOK"
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


def pending_summary():
    summary = []
    for secret_key, roles in pending.items():
        role_names = [ROLE_NAMES.get(role, str(role)) for role in roles]
        summary.append(f"secret_len={len(secret_key)} roles={role_names}")
    return "; ".join(summary) if summary else "empty"


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


async def handle_client(reader, writer, role):
    conn_id = next(connection_ids)
    peer = writer.get_extra_info("peername")
    sockname = writer.get_extra_info("sockname")
    role_name = ROLE_NAMES[role]
    logging.info("conn=%s accepted role=%s peer=%s local=%s pending=%s", conn_id, role_name, peer, sockname, pending_summary())

    paired = False
    try:
        entry = pending.setdefault(relay_secret, {})
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
                pending.pop(relay_secret, None)
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
    host_server = await asyncio.start_server(lambda r, w: handle_client(r, w, ROLE_HOST), HOST, PORT)
    guest_server = await asyncio.start_server(lambda r, w: handle_client(r, w, ROLE_GUEST), HOST, PORT + 1)
    logging.info("relay host listener on %s:%d", HOST, PORT)
    logging.info("relay guest listener on %s:%d", HOST, PORT + 1)
    logging.info("relay secret: %s", relay_secret)
    logging.info("relay secret length: %d", len(relay_secret))
    logging.info("relay secret file: %s", SECRET_FILE)
    logging.warning("relay secret is currently not enforced; loader cannot send pre-pair auth bytes")
    async with host_server, guest_server:
        await asyncio.gather(host_server.serve_forever(), guest_server.serve_forever())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    relay_secret = load_or_create_secret()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("relay stopped")
