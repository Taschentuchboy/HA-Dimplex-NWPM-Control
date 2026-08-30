# Dimplex Heat Pump – NWPM Modbus TCP

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/Taschentuchboy/HA-Dimplex-NWPM-Control/actions/workflows/validate.yml/badge.svg)](https://github.com/Taschentuchboy/HA-Dimplex-NWPM-Control/actions/workflows/validate.yml)

Home Assistant integration for Dimplex heat pumps with the **NWPM Modbus TCP**
extension. It does more than read values – it controls the heat pump, including the
**time schedules** for setback, boost, hot water block, thermal disinfection and the
circulation pump.

---

## ⚠️ Read this before installing

> ### This integration was written entirely by an AI
>
> Everything in this repository – every line of Python, the register mapping, the
> schedule logic and this documentation – was produced by **Claude (Anthropic)**,
> based on the official Dimplex datapoint list and in conversation with the
> repository owner. **No human developer has reviewed the code.** There is no
> affiliation with, or approval from, Dimplex.
>
> ### Use at your own risk
>
> This software actively writes to the registers of your heating system. Bugs can
> lead to **wrong settings, wasted electricity, no heating, or unwanted hot water
> production**. **No warranty of any kind** is given – neither for the correctness of
> the register mapping nor for the software working at all. See also the disclaimer
> in the [MIT license](LICENSE).
>
> **In practice:**
> - After installing, check every value against your heat pump's own display.
> - Test writing with something harmless first (party hours, for example).
> - Write down your schedule settings before changing them from Home Assistant.
> - Don't rely on it blindly in winter – a heating system that stops working is a
>   real problem.
>
> Testing was done against a **simulated Modbus device** with an extensive test suite,
> plus day-to-day use on a **single real installation**. Other models, other WPM
> software generations and other hydraulic layouts may behave differently.
>
> Several serious bugs were found and fixed during development, including one that
> wrote values into the **wrong** time function when a schedule was changed. It is
> realistic to assume more are still undiscovered.

---

## Why a custom integration instead of YAML?

The time functions do **not** live at their own registers. Instead there is a
multiplexer: you write a selector value to register **5065**, and only then do
registers 5066–5081 refer to the function you selected.

| Function | Value for 5065 |
|---|---|
| Heating circuit 1 setback / boost | 1 / 2 |
| Heating circuit 2 setback / boost | 3 / 4 |
| Heating circuit 3 setback / boost | 5 / 6 |
| Hot water block | 7 |
| Thermal disinfection | 8 |
| Circulation pump | 12 |

Home Assistant's built-in Modbus integration polls its entities independently. Without
coordination, one sensor flips the selector in the middle of a write sequence – and the
value lands in the wrong schedule. This integration therefore serialises **every**
Modbus access through a single lock, verifies the selection against the device before
each access, and aborts rather than writing blindly.

## Features

### Reading

- **Temperatures** – outside, flow/return, return setpoint, hot water (actual and
  setpoint), heat source outlet, heating circuits 2/3; optionally room temperature and
  humidity, cooling circuit, solar buffer
- **Operating hours** – compressor 1/2, source pump/fan, 2nd heat generator (E10),
  heating and hot water pump, immersion heater (E9)
- **Display messages as plain text** – status, block reason, fault, sensor fault; the
  raw code stays available in the `raw_value` attribute
- **Digital inputs/outputs** – compressors, pumps, mixers, 2nd heat generator,
  immersion heater, common fault, utility block (EVU), external block
- **Heat energy** – heating, hot water, pool. Requires a heat meter (WMZ 25/32).
  Note: this is **thermal energy, not electricity consumption**.

Many optional sensors are disabled by default because they depend on accessories not
every system has. You'll find them on the device page under "+ N entities not shown".
Datapoints the installation does not provide are reported as *unavailable* rather than
as 0 – a missing meter should not look like a meter reading zero.

### Writing

- **Operating mode** – Summer, Auto, Holiday, Party, 2nd heat generator, Cooling
- **Party hours**, **holiday days**
- **Heating curve parallel shift** (−19 … +19 K)
- **Hot water target temperature**

### Schedules

For HC1 setback, HC1 boost, hot water block and circulation pump: two time windows
(start/end) and seven weekdays (Yes / No / Time 1 / Time 2). Setback and boost also
have a K value (0–19).

**Thermal disinfection is structurally different** and is modelled accordingly: only
*one* start time (no end, no second window), weekdays limited to Yes/No, and instead of
a K value a target temperature (60–85 °C).

Schedules are **not** polled cyclically – that would flip the multiplexer constantly.
They are read once at startup and updated on every change made from Home Assistant. If
something was changed on the heat pump's display, use the **"Refresh schedules"**
button.

### Temporarily suspending the hot water block

The heat pump has no "block off for N minutes" register. The bypass therefore
memorises today's weekday value, writes "No", and restores the original afterwards.

- What gets restored is the weekday that was **modified**, not "today" – a bypass
  started at 23:30 would otherwise repair the wrong day.
- The pending restore is **persisted to disk**; a Home Assistant restart resumes it or
  completes it immediately.
- Triggering it again only extends the runtime and rewrites the register in case it
  drifted in the meantime.
- If restoring fails, it is retried every 5 minutes.

Use the button, the switch (remaining time in the `ends_at` attribute) or the service
`dimplex_nwpm.bypass_hot_water_lock` with `duration` in minutes.

### Starting a thermal disinfection manually

The heat pump has no "start now" command. The integration sets the start time to "two
minutes from now", enables the weekday, and restores the original state afterwards.

The end of the run is detected via **status coil 125 ("active")**. The restore,
however, deliberately hangs off a **hard deadline (4 h)** rather than the coil – if the
coil is not readable on a given installation, the run still ends cleanly. Whether it
could be read is exposed in the `status_coil_readable` attribute. Nothing is written
back while a cycle is in progress, so a running disinfection is never cut short.

Events for automations:

| Event | Meaning |
|---|---|
| `dimplex_nwpm_thermal_disinfection_triggered` | Run scheduled |
| `dimplex_nwpm_thermal_disinfection_running` | Heat pump has started |
| `dimplex_nwpm_thermal_disinfection_finished` | Finished, with `reason` in the event |

`reason`: `completed`, `not_started`, `timeout` or `cancelled`.

### Finding undocumented datapoints

The Dimplex list only publishes part of the address space. The service
`dimplex_nwpm.scan_registers` reads a range and, on the **second** call, reports which
addresses changed. Workflow: scan → warm the probe by hand for a minute or two → scan
again. The address with the largest delta is the datapoint you're after. The scan is
read-only and refuses the range 5065–5081.

## Services

| Service | Purpose |
|---|---|
| `dimplex_nwpm.bypass_hot_water_lock` | Suspend the hot water block for `duration` minutes |
| `dimplex_nwpm.cancel_hot_water_bypass` | End the bypass immediately |
| `dimplex_nwpm.start_thermal_disinfection` | Start a disinfection run now |
| `dimplex_nwpm.cancel_thermal_disinfection` | Stop supervising, restore the schedule |
| `dimplex_nwpm.scan_registers` | Read an address range (diagnostic) |

## Installation

### Via HACS

1. HACS → menu (⋮) → *Custom repositories*
2. Add this repository with type **Integration**
3. Install "Dimplex NWPM (Modbus TCP)"
4. Restart Home Assistant

### Manually

Copy `custom_components/dimplex_nwpm/` into `config/custom_components/` and restart
Home Assistant.

### Setup

Settings → Devices & services → Add integration → "Dimplex". You need the IP address
of the NWPM module, the port (default 502), the slave ID (default 1) and a poll
interval.

**Important:** remove any existing `modbus:` YAML configuration for the same heat pump
first, so two connections don't access it simultaneously.

## Dashboard card

[`dashboard_card.yaml`](dashboard_card.yaml) contains a ready-made card with outside
temperature, hot water temperature, setpoint and operating mode, plus controls for the
bypass and the disinfection – including a confirmation dialog before starting a
disinfection run. Only built-in cards are used, no HACS frontend add-ons required.

The entity IDs in it assume the device name "Dimplex Heat Pump". Check yours under
Settings → Devices & services → Dimplex NWPM → device.

## Limitations

**Not implemented:**

- **Heating circuits 2 and 3** – their schedules use the same mechanism (mux 3/4 and
  5/6) and would be easy to add. Their heating curve and setpoints, however, need a
  **second** multiplexer (address 5082) to be set first; that is missing.
- **Pool**, **ventilation**, **Smart Grid**, **Smart-RTC+ room control**
- **Electricity consumption** – there simply is no datapoint for it. The heat energy
  values are thermal output and exceed electricity use by roughly the coefficient of
  performance. Don't add them to the Energy dashboard as an electricity source.
- **Operating principle** (monovalent / mono-energetic / bivalent) – not changeable
  over Modbus, it's a commissioning setting. Only register 5020 (parallel operation
  limit, the bivalence point) would be writable, and that is not exposed as an entity
  yet.

**Known quirks:**

- One heat pump per config entry.
- The "Time 1/2 active" status coils (125/126) sit behind the multiplexer and are not
  offered as entities.
- The hot water bypass works through the weekday register and therefore applies to the
  **whole day** until the timer resets it. If Home Assistant stays down past the
  bypass end time, the block remains disabled until it comes back.
- Coil address 125 comes from the documentation and is not verified on every system.
- Schedule writes take roughly 3 seconds because the multiplexer selection is verified
  before each access. That is intentional.
- Entity names are English only; there is no `translation_key` based naming yet. The
  config flow and services are translated (English/German).

**Software generations:** status, block reason, fault and sensor fault live at
different addresses depending on the WPM software (L/M: 103–106, J: 43/59/42,
H: 14/94/13) and use different value tables. This integration uses the **L/M set**.
Being able to read those registers therefore also confirms the L/M tables apply. If
many entries show "Unknown (n)", the system probably runs J software.

To cross-reference a status against the German manual, use the `raw_value` attribute –
it holds the numeric code as documented.

## Differences from common YAML examples

Comparing against the official datapoint list turned up errors in configurations
circulating online:

- Address 2 is the **return** temperature, not the flow. The real flow temperature is
  at 5, the return setpoint at 53.
- Address 5088 is **not** the hot water maximum temperature; it belongs to the cooling
  block of heating circuits 2/3 behind a different multiplexer. For hot water,
  address 5048 applies.
- Reads use **Read Holding Registers (FC03)**, as documented.

## The undocumented register 25

One sensor is disabled by default and named "Undocumented sensor (register 25)".
Address 25 does not appear in the Dimplex datapoint list, but returns a plausible
temperature on at least one installation – most likely the "Regenerativ" probe, i.e.
input R13 configured as a regenerative buffer sensor (a wood stove tank, for example),
which the documented address 10 does not expose when no third heating circuit is
configured.

Whether it holds anything useful on your system depends entirely on your WPM
configuration. Enable it and see, or use `scan_registers` to find the right address for
your own setup.

## Troubleshooting

```yaml
logger:
  default: warning
  logs:
    custom_components.dimplex_nwpm: debug
```

Worth looking for in the log: which pymodbus keyword was detected (`slave` vs.
`device_id`, renamed in pymodbus 4.0) and whether register 5065 can be read back.

## Contributing

Bug reports are welcome – precisely because there has been no human code review, every
real installation is an additional test case. Reports from other WPM software
generations and heat pump models are especially useful.

## License

[MIT](LICENSE)

This project is not affiliated with Glen Dimplex Deutschland GmbH. "Dimplex" is a
trademark of its respective owners.
