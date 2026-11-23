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

And per device, in `devices`:

| Key | Default | Meaning |
|---|---|---|
| `realtime` | `False` | push each punch as it happens, as well as polling |
| `serial_number` | `None` | how the listener recognises the device; without it, it falls back to matching on `ip` |

On the terminal, under **Comm → Cloud Server Setting**, point the server
address at the sync PC's IP and `STATION_LISTENER_PORT`. The handshake answers
`Realtime=1`, which is what makes the terminal POST each punch instead of
batching on a timer.

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

    python -m unittest tests.test_realtime_capture

Parsing and routing are tested without sockets; `AdmsListener` is exercised
over real HTTP on an OS-assigned port. The lifecycle and `send_punch_event`
tests load `erpnext_sync` through the sibling suite's `load_sync_module`.
