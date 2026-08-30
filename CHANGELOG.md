# Changelog

## 0.6.0

- **All user-facing text is now English.** Entity names, log messages and service
  descriptions were German before. The config flow and the services are still
  translated into German via `translations/de.json`; entity names are English only.
- The device is now named "Dimplex Heat Pump" instead of "Dimplex Wärmepumpe".
  **Existing installations keep their old entity IDs** – Home Assistant assigns entity
  IDs once and never changes them on its own. Only newly created entities get the
  English IDs. Rename the device (with "also rename entity IDs") if you want them
  aligned, or keep the old IDs and adjust the dashboard card instead.
- The sensor previously called "Ofen Speicher Temp" (register 25) is now
  "Undocumented sensor (register 25)", moved into the optional group and **disabled by
  default**, since what it returns depends entirely on the installation. Its internal
  key is unchanged, so existing entities keep working.

## 0.5.2

- A failed read of **one** time function used to mark **all** schedule entities
  unavailable. The remaining functions are now read normally, and the failed one keeps
  its last known values. It only counts as a failure if no function can be read at all.
- Schedule entity availability no longer depends on the success of the last refresh
  attempt. "Unavailable" now means "this function has never been read successfully".

## 0.5.1 – important bug fix

Fixes a bug that could **corrupt schedules**.

The time function selection (register 5065) was cached and not rewritten when the
remembered value already matched. But register 5065 belongs to the device: the heat
pump's own display uses the same multiplexer, and the WPM can reset the selection by
itself. Whenever the cache went stale, a write landed in whichever time function the
device happened to have selected.

Symptom: changing one schedule altered other values with no recognisable pattern.

The selection is now verified against the device before every access. If register 5065
reports a different function than requested, the operation aborts with an error instead
of writing to the wrong schedule. If the register cannot be read at all on a given
system, the selection is rewritten before every access instead.

**Check all your schedules after updating.** Values already changed by the bug are not
restored by the update.

## 0.5.0

- `scan_registers` service for locating undocumented datapoints, with a diff between
  two scans

## 0.4.0

- Operating hours (compressors, pumps, 2nd heat generator, immersion heater)
- Status, block reason, fault and sensor fault as plain text
- Digital inputs and outputs as binary sensors
- Heat energy totals (reassembled from three registers each)
- Additional temperature sensors, mostly disabled by default
- Datapoints the system does not provide are reported as unavailable instead of 0, and
  no longer take down the remaining entities

## 0.3.0

- Thermal disinfection can be started manually, with end detection via status coil 125
  and a hard time deadline as a fallback
- Events for automations
- Fixed device name for predictable entity IDs

## 0.2.1

- Dashboard card: template guarded against a missing `ends_at`
- Bypass switch no longer reports attributes as `null` while inactive

## 0.2.0

- Thermal disinfection and circulation pump added as schedules
- Temporary hot water block bypass (button, switch, service), persisted across
  restarts and handling the midnight rollover correctly
- Dashboard card

## 0.1.1

- pymodbus 4.x renamed the `slave` parameter to `device_id`. The correct name is now
  detected at runtime.

## 0.1.0

- Initial version: config flow, sensors, operating mode, schedules for HC1 setback,
  HC1 boost and the hot water block
