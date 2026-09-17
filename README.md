# RotorHazard Connector for FPVTrackSide

This plugin allows RotorHazard to communicate with FPVTrackSide:
* control race state from FPVTrackSide (start, abort, stop)
* automatic pilot import (enables adaptive calibration)
* send marshaled race data
* enables time and frequency setup synchronization

## Installation

Install through the "Community Plugins" area within RotorHazard. Alternately, copy the `rh_connector_trackside` directory from inside `custom_plugins` into the plugins directory of your RotorHazard data directory.

## Usage

After confirming installation, use FPVTrackSide as normal with this server's address. RotorHazard will automatically import pilots and start/stop races as directed by FPVTrackSide. RotorHazard will send revised data back to FPVTrackSide if the Marshal tool in RotorHazard is used.  

### Lean mode

Enable **Lean mode (do not save races)** in RotorHazard's Settings under
**FPVTrackSide Connector** to reuse one heat and avoid saving race history and
rebuilding results. This option defaults to off. Lap timing and the ELRS OSD
continue to work, but **adaptive calibration, marshalling and RotorHazard's own
results pages are unavailable** in lean mode. Adaptive calibration relies on
saved race history to adjust calibration points for each pilot.

The option is also available through Socket.IO commands, using acknowledgement
callbacks like `ts_server_info`:

| Command | Payload | Acknowledgement |
| --- | --- | --- |
| `ts_get_lean_mode` | None required | `{"lean_mode": false}` (current boolean value) |
| `ts_set_lean_mode` | `{"lean_mode": true}` or `{"lean_mode": false}` | `{"lean_mode": true}` or `{"lean_mode": false}` (stored value) |

The setter requires a JSON boolean. Invalid payloads return the current value
with an `error` string and leave the option unchanged. Mode changes are accepted
only when the race is ready, so a staged, running or stopped race awaiting save
cannot have its retention behaviour changed remotely. Requests for the already
configured value succeed in any race state. Successful changes update the same
option used by the checkbox and refresh the Settings UI. FPVTrackSide support
for these commands can be added independently.
