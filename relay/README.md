
Heavy WIP.

Create a local secret file on the relay host:

```sh
printf '%s\n' 'replace-with-a-long-random-secret' > relay_secret.txt
```

Create the same file on each Wii SD card:

```text
sd:/apps/netslug/relay_secret.txt
```
Run the server w/ 
```sh
python3 relay/netslug_relay.py
```
and set optional env variables:
```sh
NETSLUG_RELAY_BIND_ADDR=0.0.0.0
NETSLUG_RELAY_PORT=10000
NETSLUG_RELAY_SECRET_FILE=relay_secret.txt
NETSLUG_RELAY_MATCH_TIMEOUT_SECS=120
NETSLUG_RELAY_LOG_LEVEL=INFO
NETSLUG_RELAY_PROGRESS_INTERVAL_SECS=10
NETSLUG_RELAY_DRAIN_WARN_SECS=5
NETSLUG_RELAY_IDLE_WARN_SECS=10
NETSLUG_RELAY_CHUNK_LOG_LIMIT=8
NETSLUG_RELAY_CHUNK_PREVIEW_BYTES=64
NETSLUG_RELAY_STARTUP_STALL_SECS=30
NETSLUG_RELAY_STARTUP_MIN_BYTES=4
```
This only works if you set up the config.ini to use relay mode.

```ini
[network]
relay_enabled = yes
relay_ip = 203.0.113.10
relay_port = 10000
```

The host still presses A and the guest still presses B.

Useful log lines:

```text
accepted host 198.51.100.10:43001
accepted guest 203.0.113.20:50244
session 1 paired host=198.51.100.10:43001 guest=203.0.113.20:50244
debug 1 connected peer=198.51.100.10:43002 role=host
debug 1 host 198.51.100.10:43002: loader exported module symbols role=host game_sock=3 debug_sock=4 net_fd=7
debug 1 host 198.51.100.10:43002: module sendThread started role=host
session 1 host->guest chunk=1 len=4 total=4 first_bytes=01 00 00 00
session 1 guest->host chunk=1 len=4 total=4 first_bytes=01 00 00 00
session 1 startup stalled for 30.0s: host->guest=4 bytes/1 chunks guest->host=4 bytes/1 chunks; closing both peers
session 1 closed host=198.51.100.10:43001 guest=203.0.113.20:50244 totals host->guest=4/1 guest->host=4/1
```

If you're debugging use NSD1 instead of NSR1
