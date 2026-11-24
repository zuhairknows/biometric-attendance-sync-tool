# Station terminals: realtime punch push

A station terminal is a biometric reader with a screen beside it that has to
react to a punch — an operator confirming production at a machine, who then
expects a label out of the printer on the bench. The five-minute
`PULL_FREQUENCY` poll is right for attendance and far too slow for that, so a
few named devices get a second, sub-second path.

The poll is untouched. A realtime device stays in `devices` and is still polled
exactly as before; that is what keeps attendance correct when the push path is
down. The push creates no Employee Checkin of its own — the poll still does
that, and the duplicate it would otherwise cause is the reason this path leaves
it alone.

## Why ADMS and not `live_capture()`

pyzk's `live_capture()` holds an SDK connection open on port 4370 for the whole
shift, and the attendance poll needs that same port every five minutes. ZK
terminals are unreliable about concurrent SDK connections, so a held connection
would put a working attendance sync at risk to gain a feature.

ADMS is the opposite shape: the device dials out over HTTP when somebody
punches and hangs up again, so nothing is held, and it survives NAT. The cost
is a change on the device itself, which is deliberate — it is reversible from
the terminal's own menu and leaves every other device in the fleet alone.

## Configuration

Nothing here is on by default. With `STATION_PUNCH_METHOD` empty nothing is
ever posted, and with no device flagged `realtime` **no port is opened at all**.

In `local_config.py`:

| Setting | Default | Meaning |
|---|---|---|
| `STATION_PUNCH_METHOD` | `''` | whitelisted ERPNext method a punch is posted to; empty disables the push |
| `STATION_PUNCH_TIMEOUT` | `5` | seconds to wait for it — short on purpose, somebody is standing at the machine |
| `STATION_LISTENER_HOST` | `'0.0.0.0'` | where the listener binds; the terminals must reach it, so restrict it to the device VLAN at the firewall |
| `STATION_LISTENER_PORT` | `8090` | the listener's port |
| `STATION_PUNCH_MAX_AGE_SECONDS` | `300` | a punch timed further than this from the sync PC clock, either way, is acknowledged but not posted |
| `STATION_DEVICES` | `{}` | station terminals as `device_id -> serial`; the one-line alternative to the two keys below. A `device_id` not in `devices` is a **station-only** terminal: recognised by its serial, never polled, so its punches are not attendance |

And per device, in `devices`:

| Key | Default | Meaning |
|---|---|---|
| `realtime` | `False` | push each punch as it happens, as well as polling |
| `serial_number` | `None` | how the listener recognises the device; without it, it falls back to matching on `ip` |

On the terminal, under **Comm → Cloud Server Setting**, point the server
address at the sync PC's IP and `STATION_LISTENER_PORT`. The handshake answers
`Realtime=1`, which is what makes the terminal POST each punch instead of
batching on a timer.

## Stored punches and clocks

A terminal newly switched to ADMS may upload the punches it already holds. The
poll recorded those as attendance long ago, and firing the station action for
each would print a label per old punch. So a punch timed more than
`STATION_PUNCH_MAX_AGE_SECONDS` from the sync PC's clock, in either direction,
is acknowledged to the device (so it is not offered again) and skipped, with
`Skipped ATTLOG rows outside the station age limit` in `logs.log`. A station
terminal's clock therefore has to be right; one that is out by more than the
limit is ignored until it is corrected.

## Installing on the sync PC

`deployment/Install-StationTerminal.ps1` (elevated, dry run unless `-Apply`)
installs `erpnext_sync.py` and `realtime_capture.py` into the Phase 2 service
folder, only over the reviewed `erpnext_sync.py`, with a backup and automatic
rollback. Copy it into one folder with those two files from this branch.

1. Files only; inert, no port opened:

       .\Install-StationTerminal.ps1 -Apply

2. One terminal, once the ERPNext method exists:

       .\Install-StationTerminal.ps1 -PunchMethod <app.module.method> -Stations FP2_DEVICE_12=<serial> -AllowFrom 10.0.1.0/24 -Apply

   `-Stations` is the complete list each time. `-AllowFrom` creates the inbound
   firewall rule *FPF Biometric Station ADMS* for the listener port.

3. On the terminal, **Comm -> Cloud Server Setting**: server address = sync PC
   IP, port = 8090. Check `logs.log` for `ADMS listener started`, `ADMS
   handshake`, then `Station punch delivered` on a punch, and that the device is
   still polled normally on the dashboard.

To switch it off: turn Cloud Server off on the terminal, then
`.\Install-StationTerminal.ps1 -Disable -Apply` (clears `STATION_DEVICES` and
removes the firewall rule).

## What the listener does

`realtime_capture.py` serves the ADMS conversation — `/iclock/cdata`,
`/iclock/getrequest`, `/iclock/devicecmd` — and `erpnext_sync.main()` owns its
lifecycle, because `main()` is the one thing every entry point calls. It starts
before the durable/legacy split and before the `PULL_FREQUENCY` gate, so the
listener is up from the first cycle in either sync mode. Starting is idempotent
and never raises: a start that failed (a port briefly in use) is retried on the
next cycle rather than lost until a restart.

Three behaviours worth knowing:

* **An unknown serial is refused with 401.** This path creates ERP records, so
  it accepts only configured devices, matched on `serial_number` first and `ip`
  second. A device configured without `realtime` is refused as well.
* **A failing callback still answers `OK`.** Otherwise the terminal replays
  punches that were already handled.
* **A malformed ATTLOG row is skipped and counted**, not rejected. Firmware
  adds columns on the right over time, and one unreadable row must not cost the
  rest of the batch.

## What a station punch is not

It is **never retried**, and nothing about it reaches the ledger. The operator
is standing at the machine; a label that arrives two minutes late is worse than
one that never comes, because by then they have moved on and the pack is
unlabelled. A failure is logged loudly in `error.log` as
`Station punch refused; not retried.` or `Station punch transport failure;`
and that is the end of it. The punch itself is still safe — the poll records it
as attendance.

## Tests

    python -m unittest discover -s tests -p test_realtime_capture.py

Parsing and routing are tested without sockets; `AdmsListener` is exercised
over real HTTP on an OS-assigned port. The lifecycle and `send_punch_event`
tests load `erpnext_sync` through the sibling suite's `load_sync_module`.
