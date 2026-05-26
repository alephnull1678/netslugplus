
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
```
This only works if you set up the config.ini to use relay mode.

```ini
[network]
relay_enabled = yes
relay_ip = 203.0.113.10
relay_port = 10000
```

The host still presses A and the guest still presses B.
