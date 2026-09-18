# RotorHazard Connector for FPVTrackSide

This plugin allows RotorHazard to communicate with FPVTrackSide:

- control race state from FPVTrackSide (start, abort, stop)
- automatic pilot import (enables adaptive calibration)
- send marshaled race data
- enables time and frequency setup synchronization

## Installation

Install through the "Community Plugins" area within RotorHazard. Alternately, copy the `rh_connector_trackside` directory from inside `custom_plugins` into the plugins directory of your RotorHazard data directory.

## Usage

After confirming installation, use FPVTrackSide as normal with this server's address. RotorHazard will automatically import pilots and start/stop races as directed by FPVTrackSide. RotorHazard will send revised data back to FPVTrackSide if the Marshal tool in RotorHazard is used.

### Preventing History

You can enable _Prevent Race Saving_ in the _FPVTrackSide Connector_ panel to reuse a single heat without saving to RotorHazard's database. This will disable features that rely on race history, such as marshaling, adaptive calibration, and race results. In this mode, the timer can maintain full responsiveness indefinitely even when running on low-performance server hardware.
