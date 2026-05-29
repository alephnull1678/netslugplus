
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
```
This only works if you set up the config.ini to use relay mode.

```ini
[network]
relay_enabled = yes
relay_ip = 203.0.113.10
relay_port = 10000
```

The host still presses A and the guest still presses B.

You can view live logs with:

```sh
sudo journalctl -u netslug-relay.service -f
```

If your service has a different name, find it with:

```sh
systemctl list-units --type=service | grep -i netslug
```

Useful log lines:

```text
accepted host 198.51.100.10:43001
accepted guest 203.0.113.20:50244
session 1 paired host=198.51.100.10:43001 guest=203.0.113.20:50244
session 1 host->guest transferred 1234 bytes in 4 chunks over 10.0s
session 1 guest->host transferred 1234 bytes in 4 chunks over 10.0s
session 1 guest->host idle for more than 10.0s after 0 bytes in 0 chunks
session 1 host->guest drain blocked for more than 5.0s after 8192 bytes
session 1 closed host=198.51.100.10:43001 guest=203.0.113.20:50244
```
