"""Constants and Modbus register maps for the Dimplex NWPM integration.

Register addresses are taken from the Dimplex "NWPM Modbus TCP" datapoint
list, WPM-Software column "J/L/M" (this matches the addresses that were
confirmed working in the user's original modbus.yaml: outside temp=1,
hot water temp=3, parallel displacement=5036, party hours=5016).

IMPORTANT / KNOWN CAVEATS (see README.md for details):
- The original modbus.yaml read "Heizkreis Vorlauf Temp" and "Heizkreis Soll
  Temp" both from address 2. Per the official datapoint list, address 2 is
  actually "Temperatur Ruecklauf" (return temperature), the real Vorlauf
  (flow) temperature is address 5, and the real Ruecklaufsoll (return
  setpoint) is address 53. This integration uses the corrected addresses.
- The original "Dimplex Max temperature" at address 5088 does not match the
  documented Warmwasser-Solltemperatur-Maximal (which is 5048). Address 5088
  belongs to the heating circuit 2/3 cooling block and is only meaningful after
  selecting that circuit via a *different* multiplexer (address 5082). It
  has been dropped from this integration; 5048 is used instead for the hot
  water max-temperature sensor.
- Address 25 is not part of the official Dimplex register list. It is exposed
  as an optional, disabled-by-default sensor because it returns a plausible
  temperature on at least one installation. See OPTIONAL_SENSOR_REGISTERS.
"""

from __future__ import annotations

from dataclasses import dataclass

DOMAIN = "dimplex_nwpm"

CONF_SLAVE = "slave"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 502
DEFAULT_SLAVE = 1
DEFAULT_SCAN_INTERVAL = 30  # seconds, for the "normal" (non-scheduled) values

MANUFACTURER = "Dimplex"
MODEL = "Heat pump manager (NWPM Modbus TCP)"

# Fixed device name so generated entity_ids are predictable and stable
# (e.g. sensor.dimplex_warmepumpe_aussentemperatur) regardless of the host
# address entered during setup. This keeps the dashboard YAML portable.
DEVICE_NAME = "Dimplex Heat Pump"

# Delay (seconds) to wait after switching the time-function multiplexer
# (register 5065 / 5082) before reading or writing the data registers behind
# it. Dimplex's own documentation recommends ~3s for the structurally
# identical room-address multiplexer used by Smart-RTC+ (chapter 6.1.3).
MUX_SETTLE_DELAY = 3.0

# ---------------------------------------------------------------------------
# "Normal" (non-multiplexed) holding registers, polled periodically
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegisterDef:
    key: str
    name: str
    address: int
    scale: float = 1.0
    precision: int = 0
    unit: str | None = None
    device_class: str | None = None
    signed: bool = False


SENSOR_REGISTERS: tuple[RegisterDef, ...] = (
    # -- Temperaturen (Float16 in Zehntelgrad, vorzeichenbehaftet) ---------
    RegisterDef("outside_temp", "Outside temperature", 1, 0.1, 1, "°C", "temperature", True),
    RegisterDef("return_temp", "Return temperature", 2, 0.1, 1, "°C", "temperature", True),
    RegisterDef("hot_water_temp", "Hot water temperature", 3, 0.1, 1, "°C", "temperature", True),
    RegisterDef("flow_temp", "Flow temperature", 5, 0.1, 1, "°C", "temperature", True),
    RegisterDef("source_out_temp", "Heat source outlet", 7, 0.1, 1, "°C", "temperature", True),
    RegisterDef("hk2_temp", "Heating circuit 2 temperature", 9, 0.1, 1, "°C", "temperature", True),
    # R13 is shared: it reads either the 3rd heating circuit or, if the WPM is
    # configured that way, the "Regenerativ" probe - i.e. the buffer fed by a
    # wood stove or similar. Compare this against the undocumented address 25
    # to find out which one your installation actually uses (see README).
    RegisterDef("hk3_or_regen_temp", "Heating circuit 3 / regenerative temperature (R13)", 10, 0.1, 1, "°C", "temperature", True),
    RegisterDef("return_setpoint_temp", "Return setpoint temperature", 53, 0.1, 1, "°C", "temperature", True),
    RegisterDef("hk2_setpoint_temp", "Heating circuit 2 setpoint", 54, 0.1, 1, "°C", "temperature", True),
    RegisterDef("hot_water_setpoint_temp_effective", "Hot water setpoint (effective)", 58, 0.1, 1, "°C", "temperature", True),
)

# Optional temperature sensors: only present on some installations (source
# inlet needs an electronic expansion valve, solar/room sensors need the
# corresponding accessories). Disabled by default so they do not clutter the
# device page with "unknown" entities on systems that lack them.
OPTIONAL_SENSOR_REGISTERS: tuple[RegisterDef, ...] = (
    # Address 25 is NOT in the official Dimplex datapoint list. On at least one
    # installation it returns a real temperature - most likely the
    # "Regenerativ" probe (R13 configured as a regenerative buffer sensor, e.g.
    # a wood stove tank), which the documented address 10 does not expose when
    # no 3rd heating circuit is configured. Whether it holds anything useful
    # depends entirely on how the WPM is set up, so it is disabled by default.
    # Use the scan_registers service to find the address on your own system.
    RegisterDef("buffer_temp_custom", "Undocumented sensor (register 25)", 25, 0.1, 1, "°C", "temperature", True),
    RegisterDef("source_in_temp", "Heat source inlet (R24)", 6, 0.1, 1, "°C", "temperature", True),
    RegisterDef("room_temp_1", "Room temperature 1", 11, 0.1, 1, "°C", "temperature", True),
    RegisterDef("room_temp_2", "Room temperature 2", 12, 0.1, 1, "°C", "temperature", True),
    RegisterDef("room_humidity_1", "Room humidity 1", 13, 0.1, 1, "%", "humidity", True),
    RegisterDef("room_humidity_2", "Room humidity 2", 14, 0.1, 1, "%", "humidity", True),
    RegisterDef("cooling_flow_temp", "Cooling flow temperature (R11)", 19, 0.1, 1, "°C", "temperature", True),
    RegisterDef("cooling_return_temp", "Cooling return temperature (R4)", 20, 0.1, 1, "°C", "temperature", True),
    RegisterDef("solar_buffer_temp", "Solar buffer (R22)", 23, 0.1, 1, "°C", "temperature", True),
    RegisterDef("hk3_setpoint_temp", "Heating circuit 3 setpoint", 55, 0.1, 1, "°C", "temperature", True),
)

# ---------------------------------------------------------------------------
# Operating-hour counters (uint16, cumulative). These are the quickest way to
# find out what a system actually has installed: a non-zero counter on the
# 2nd heat generator proves the WPM has been switching something on, which a
# wood stove can never be - the WPM cannot light a fire.
# ---------------------------------------------------------------------------

RUNTIME_REGISTERS: tuple[RegisterDef, ...] = (
    RegisterDef("hours_compressor_1", "Compressor 1 operating hours", 72, 1.0, 0, "h"),
    RegisterDef("hours_compressor_2", "Compressor 2 operating hours", 73, 1.0, 0, "h"),
    RegisterDef("hours_source_pump", "Source pump / fan operating hours", 74, 1.0, 0, "h"),
    RegisterDef("hours_second_heater", "2nd heat generator (E10) operating hours", 75, 1.0, 0, "h"),
    RegisterDef("hours_heating_pump", "Heating pump (M13) operating hours", 76, 1.0, 0, "h"),
    RegisterDef("hours_hot_water_pump", "Hot water pump (M18) operating hours", 77, 1.0, 0, "h"),
    RegisterDef("hours_flange_heater", "Immersion heater (E9) operating hours", 78, 1.0, 0, "h"),
)

# ---------------------------------------------------------------------------
# Heat energy totals. Each is split across three registers holding four
# decimal digits each, and has to be reassembled as
#   9-12 * 100_000_000  +  5-8 * 10_000  +  1-4
# Only available with an integrated or external heat meter (WMZ 25/32);
# without one these read zero, so they are disabled by default.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnergyDef:
    key: str
    name: str
    addr_low: int    # digits 1-4
    addr_mid: int    # digits 5-8
    addr_high: int   # digits 9-12


ENERGY_REGISTERS: tuple[EnergyDef, ...] = (
    EnergyDef("energy_heating", "Heat energy heating", 5096, 5097, 5098),
    EnergyDef("energy_hot_water", "Heat energy hot water", 5099, 5100, 5101),
    EnergyDef("energy_pool", "Heat energy pool", 5102, 5103, 5104),
)

ENERGY_DIGIT_BASE_MID = 10_000
ENERGY_DIGIT_BASE_HIGH = 100_000_000


# Contiguous block 5015-5017: Betriebsmodus, Partystunden, Urlaubstage
SETTINGS_BLOCK_START = 5015
SETTINGS_BLOCK_COUNT = 3

OPERATING_MODES = {
    0: "Summer",
    1: "Auto",
    2: "Holiday",
    3: "Party",
    4: "2nd heat generator",
    5: "Cooling",
}

ADDR_MODE = 5015
ADDR_PARTY_HOURS = 5016
ADDR_HOLIDAY_DAYS = 5017
ADDR_PARALLEL_DISPLACEMENT = 5036
ADDR_HOT_WATER_TARGET_TEMP = 5047
ADDR_HOT_WATER_MAX_TEMP = 5048

# Parallel shift: raw register value 0-38 maps to a real offset of -19..+19 K
PARALLEL_DISPLACEMENT_OFFSET = 19

# ---------------------------------------------------------------------------
# Multiplexed time-function registers (register 5065 selects the function,
# registers 5066-5081 then refer to whichever function was last selected)
# ---------------------------------------------------------------------------

MUX_SELECT_ADDRESS = 5065
BLOCK_START = 5066  # first register of the shared time-function block

# Offsets relative to BLOCK_START. For each clock time the hour sits at the
# given offset and the minute at offset + 1.
OFF_START_HOUR_1 = 0
OFF_END_HOUR_1 = 2
OFF_START_HOUR_2 = 4
OFF_END_HOUR_2 = 6
OFF_SUNDAY = 8
OFF_MONDAY = 9
OFF_TUESDAY = 10
OFF_WEDNESDAY = 11
OFF_THURSDAY = 12
OFF_FRIDAY = 13
OFF_SATURDAY = 14
OFF_VALUE = 15

# Ordered Sunday-first to match the register layout (5074 = Sonntag).
# `py_weekday` is Python's date.weekday() value (Mon=0 .. Sun=6) so the
# hot-water bypass can map "today" onto the correct register.
WEEKDAY_OFFSETS: tuple[tuple[str, str, int, int], ...] = (
    ("sunday", "Sunday", OFF_SUNDAY, 6),
    ("monday", "Monday", OFF_MONDAY, 0),
    ("tuesday", "Tuesday", OFF_TUESDAY, 1),
    ("wednesday", "Wednesday", OFF_WEDNESDAY, 2),
    ("thursday", "Thursday", OFF_THURSDAY, 3),
    ("friday", "Friday", OFF_FRIDAY, 4),
    ("saturday", "Saturday", OFF_SATURDAY, 5),
)

# Weekday behaviour options. Schedule-style functions offer four choices;
# thermal disinfection only two (documented range 0-1).
WEEKDAY_MODES_FULL = {0: "Yes", 1: "No", 2: "Time 1", 3: "Time 2"}
WEEKDAY_MODES_BINARY = {0: "Yes", 1: "No"}

# Value written to a weekday register to disable the function on that day.
WEEKDAY_MODE_OFF = 1  # "No"


@dataclass(frozen=True)
class TimeWindowDef:
    """One editable clock time, backed by an hour+minute register pair."""

    key: str
    label: str
    offset: int


@dataclass(frozen=True)
class ValueDef:
    """The optional extra register at the end of a time-function block."""

    label: str
    offset: int
    min_value: int
    max_value: int
    unit: str
    device_class: str | None = None


# Most functions expose two start/end time windows.
STANDARD_WINDOWS: tuple[TimeWindowDef, ...] = (
    TimeWindowDef("window1_start", "Window 1 start", OFF_START_HOUR_1),
    TimeWindowDef("window1_end", "Window 1 end", OFF_END_HOUR_1),
    TimeWindowDef("window2_start", "Window 2 start", OFF_START_HOUR_2),
    TimeWindowDef("window2_end", "Window 2 end", OFF_END_HOUR_2),
)

# Thermal disinfection is a one-shot trigger: it only has a start time.
DISINFECTION_WINDOWS: tuple[TimeWindowDef, ...] = (
    TimeWindowDef("start", "Start time", OFF_START_HOUR_1),
)


@dataclass(frozen=True)
class ScheduleFunctionDef:
    key: str
    name: str
    mux_value: int
    block_length: int
    windows: tuple[TimeWindowDef, ...]
    weekday_modes: dict[int, str]
    value: ValueDef | None = None


# Key of the hot-water lock, referenced by the temporary-bypass feature.
WW_SPERRE_KEY = "ww_sperre"

# Key of the thermal disinfection, referenced by the manual "run now" feature.
THERMAL_DISINFECTION_KEY = "thermische_desinfektion"

SCHEDULE_FUNCTIONS: tuple[ScheduleFunctionDef, ...] = (
    ScheduleFunctionDef(
        key="hk1_absenkung",
        name="HC1 setback",
        mux_value=1,
        block_length=16,
        windows=STANDARD_WINDOWS,
        weekday_modes=WEEKDAY_MODES_FULL,
        value=ValueDef("setback value", OFF_VALUE, 0, 19, "K"),
    ),
    ScheduleFunctionDef(
        key="hk1_anhebung",
        name="HC1 boost",
        mux_value=2,
        block_length=16,
        windows=STANDARD_WINDOWS,
        weekday_modes=WEEKDAY_MODES_FULL,
        value=ValueDef("boost value", OFF_VALUE, 0, 19, "K"),
    ),
    ScheduleFunctionDef(
        key=WW_SPERRE_KEY,
        name="Hot water block",
        mux_value=7,
        block_length=15,
        windows=STANDARD_WINDOWS,
        weekday_modes=WEEKDAY_MODES_FULL,
        value=None,
    ),
    ScheduleFunctionDef(
        key=THERMAL_DISINFECTION_KEY,
        name="Thermal disinfection",
        mux_value=8,
        block_length=16,
        windows=DISINFECTION_WINDOWS,
        weekday_modes=WEEKDAY_MODES_BINARY,
        value=ValueDef("temperature", OFF_VALUE, 60, 85, "°C", "temperature"),
    ),
    ScheduleFunctionDef(
        key="zirkulationspumpe",
        name="Circulation pump",
        mux_value=12,
        block_length=15,
        windows=STANDARD_WINDOWS,
        weekday_modes=WEEKDAY_MODES_FULL,
        value=None,
    ),
)

SCHEDULE_FUNCTIONS_BY_KEY = {f.key: f for f in SCHEDULE_FUNCTIONS}

# ---------------------------------------------------------------------------
# Temporary hot-water-lock bypass
# ---------------------------------------------------------------------------

DEFAULT_BYPASS_MINUTES = 60
SERVICE_BYPASS_HOT_WATER_LOCK = "bypass_hot_water_lock"
SERVICE_CANCEL_HOT_WATER_BYPASS = "cancel_hot_water_bypass"
ATTR_DURATION = "duration"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
STORAGE_VERSION = 1
SIGNAL_BYPASS_UPDATED = f"{DOMAIN}_bypass_updated"

# ---------------------------------------------------------------------------
# Manual thermal-disinfection run
# ---------------------------------------------------------------------------

# Status coil behind the multiplexer. For thermal disinfection (mux 8) coil
# 125 is documented simply as "Aktiv" (0 = inaktiv, 1 = aktiv).
COIL_DISINFECTION_ACTIVE = 125

# Value written to a weekday register to enable the function on that day.
WEEKDAY_MODE_ON = 0  # "Yes"

# Minutes into the future the start time is set to. The heat pump evaluates
# the schedule on minute boundaries, so a small lead avoids setting a time
# that has already passed by the time the write lands.
DISINFECTION_LEAD_MINUTES = 2

# How often the status coil is polled while a manual run is in progress.
DISINFECTION_POLL_SECONDS = 300

# If the coil never reports "aktiv" within this window after the scheduled
# start, assume the heat pump declined to run (e.g. tank already hot enough)
# and restore the schedule.
DISINFECTION_START_TIMEOUT_MINUTES = 30

# Hard cap. Disinfection normally takes 1-2 h; this is the point at which the
# schedule gets restored regardless of what the coil says, so a stuck or
# unreadable coil can never leave the schedule permanently modified.
DISINFECTION_MAX_MINUTES = 240

SERVICE_START_THERMAL_DISINFECTION = "start_thermal_disinfection"
SERVICE_CANCEL_THERMAL_DISINFECTION = "cancel_thermal_disinfection"

EVENT_DISINFECTION_TRIGGERED = f"{DOMAIN}_thermal_disinfection_triggered"
EVENT_DISINFECTION_RUNNING = f"{DOMAIN}_thermal_disinfection_running"
EVENT_DISINFECTION_FINISHED = f"{DOMAIN}_thermal_disinfection_finished"

# ---------------------------------------------------------------------------
# Display messages (status / block reason / fault / sensor fault)
#
# IMPORTANT: these four registers live at *different addresses per WPM
# software generation* (L/M = 103-106, J = 43/59/42/-, H = 14/94/13/-), and
# the meaning of the values differs too. The addresses below are the L/M set,
# which matches the rest of this integration. Successfully reading 103 is
# therefore itself the confirmation that the L/M value tables apply.
# ---------------------------------------------------------------------------

ADDR_STATUS = 103
ADDR_BLOCK_REASON = 104
ADDR_FAULT = 105
ADDR_SENSOR_FAULT = 106

# 5.5.1 Status messages, L/M software column
STATUS_MESSAGES = {
    0: "Off",
    1: "Off",
    2: "Heating",
    3: "Pool",
    4: "Hot water",
    5: "Cooling",
    10: "Defrosting",
    11: "Flow monitoring",
    24: "Operating mode switch delay",
    30: "Blocked",
}

# 5.5.2 Block reasons, L/M software column
BLOCK_REASONS = {
    0: "Not blocked",
    2: "Flow rate",
    5: "Function check",
    6: "High-temperature operating limit",
    7: "System check",
    8: "Cooling changeover delay",
    9: "Pump pre-run",
    10: "Minimum idle time",
    11: "Utility load control",
    12: "Cycle rate limit",
    13: "Hot water reheating",
    14: "Regenerative source",
    15: "Utility block (EVU)",
    16: "Soft starter",
    17: "Flow",
    18: "Heat pump operating limit",
    19: "High pressure",
    20: "Low pressure",
    21: "Heat source operating limit",
    23: "System limit",
    24: "Primary circuit load",
    25: "External block",
    31: "Warming up",
    33: "EEV initialisation",
    34: "2nd heat generator enabled",
    35: "Fault",
}

# 5.5.3 Fault messages, L/M software column. A leading "!" in the manual marks
# the acknowledged/latched variant of the same fault; kept as "(quittiert)".
FAULT_MESSAGES = {
    0: "No fault",
    1: "Fault N17.1",
    2: "Fehler N17.2",
    3: "Fehler N17.3",
    4: "Fehler N17.4",
    6: "Electronic expansion valve",
    10: "WPIO",
    12: "Inverter",
    13: "WQIF",
    15: "Sensors",
    16: "Low pressure brine",
    19: "Primary circuit (acknowledged)",
    20: "Defrosting (acknowledged)",
    21: "Low pressure brine (acknowledged)",
    22: "Hot water (acknowledged)",
    23: "Compressor load (acknowledged)",
    24: "Coding (acknowledged)",
    25: "Low pressure (acknowledged)",
    26: "Frost protection (acknowledged)",
    28: "High pressure (acknowledged)",
    29: "Temperature difference (acknowledged)",
    30: "Hot gas thermostat (acknowledged)",
    31: "Flow (acknowledged)",
}

# 5.5.4 Sensor faults, L/M software column
SENSOR_FAULTS = {
    0: "kein Fehler",
    1: "Outside sensor (R1)",
    2: "Return sensor (R2)",
    3: "Hot water sensor (R3)",
    4: "Coding (R7)",
    5: "Flow sensor (R9)",
    6: "Heating circuit 2 sensor (R5)",
    7: "Heating circuit 3 sensor (R13)",
    8: "Regenerative sensor (R13)",
    9: "Room sensor 1",
    10: "Room sensor 2",
    11: "Heat source outlet sensor (R6)",
    12: "Heat source inlet sensor (R24)",
    14: "Collector sensor (R23)",
    15: "Low pressure sensor (R25)",
    16: "High pressure sensor (R26)",
    17: "Room humidity 1",
    18: "Room humidity 2",
    19: "Cooling frost protection sensor",
    20: "Hot gas",
    21: "Return sensor (R2.1)",
    22: "Pool sensor (R20)",
    23: "Passive cooling flow sensor (R11)",
    24: "Passive cooling return sensor (R4)",
    26: "Solar buffer sensor (R22)",
    28: "Heating demand sensor (R2.2)",
    29: "RTM Econ",
    30: "Cooling demand sensor (R39)",
}


@dataclass(frozen=True)
class EnumSensorDef:
    key: str
    name: str
    address: int
    mapping: dict[int, str]
    icon: str | None = None


ENUM_SENSORS: tuple[EnumSensorDef, ...] = (
    EnumSensorDef("status", "Status", ADDR_STATUS, STATUS_MESSAGES, "mdi:state-machine"),
    EnumSensorDef("block_reason", "Block reason", ADDR_BLOCK_REASON, BLOCK_REASONS, "mdi:lock-outline"),
    EnumSensorDef("fault", "Fault", ADDR_FAULT, FAULT_MESSAGES, "mdi:alert-circle-outline"),
    EnumSensorDef("sensor_fault", "Sensor fault", ADDR_SENSOR_FAULT, SENSOR_FAULTS, "mdi:thermometer-alert"),
)

# ---------------------------------------------------------------------------
# 5.6 Inputs / 5.7 Outputs - plain coils, NOT behind the multiplexer.
# The 2nd heat generator output (coil 44) is the live counterpart to the
# operating-hours counter: it shows whether it is switched on right now.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoilDef:
    key: str
    name: str
    address: int
    device_class: str | None = None
    icon: str | None = None
    enabled_default: bool = True


INPUT_COILS: tuple[CoilDef, ...] = (
    CoilDef("in_hot_water_thermostat", "Hot water thermostat input", 3, None, "mdi:electric-switch"),
    CoilDef("in_pool_thermostat", "Pool thermostat input", 4, None, "mdi:electric-switch", False),
    CoilDef("in_utility_block", "Utility block input (EVU)", 5, None, "mdi:transmission-tower-off"),
    CoilDef("in_external_block", "External block input", 6, None, "mdi:lock-outline"),
)

OUTPUT_COILS: tuple[CoilDef, ...] = (
    CoilDef("out_compressor_1", "Compressor 1", 41, "running", "mdi:heat-pump-outline"),
    CoilDef("out_compressor_2", "Compressor 2", 42, "running", "mdi:heat-pump-outline", False),
    CoilDef("out_source_pump", "Source pump / fan", 43, "running", "mdi:fan"),
    CoilDef("out_second_heater", "2nd heat generator (E10)", 44, "running", "mdi:radiator"),
    CoilDef("out_heating_pump_m13", "Heating pump (M13)", 45, "running", "mdi:pump"),
    CoilDef("out_hot_water_pump", "Hot water pump (M18)", 46, "running", "mdi:pump"),
    CoilDef("out_mixer_m21_open", "Mixer (M21) open", 47, None, "mdi:valve-open", False),
    CoilDef("out_mixer_m21_close", "Mixer (M21) close", 48, None, "mdi:valve-closed", False),
    CoilDef("out_aux_pump", "Auxiliary circulation pump (M16)", 49, "running", "mdi:pump", False),
    CoilDef("out_flange_heater", "Immersion heater (E9)", 50, "running", "mdi:heating-coil"),
    CoilDef("out_heating_pump_m15", "Heating pump (M15)", 51, "running", "mdi:pump", False),
    CoilDef("out_mixer_m22_open", "Mixer (M22) open", 52, None, "mdi:valve-open", False),
    CoilDef("out_mixer_m22_close", "Mixer (M22) close", 53, None, "mdi:valve-closed", False),
    CoilDef("out_pool_pump", "Pool pump (M19)", 56, "running", "mdi:pump", False),
    CoilDef("out_common_fault", "Common fault (H5)", 57, "problem", "mdi:alert"),
    CoilDef("out_heating_pump_m14", "Heating pump (M14)", 59, "running", "mdi:pump", False),
)

# ---------------------------------------------------------------------------
# Register scan (finding undocumented datapoints)
# ---------------------------------------------------------------------------

SERVICE_SCAN_REGISTERS = "scan_registers"
ATTR_START = "start"
ATTR_END = "end"

SCAN_DEFAULT_START = 1
SCAN_DEFAULT_END = 130
SCAN_MAX_SPAN = 256

# Registers behind the time-function multiplexer must not be scanned: their
# meaning depends on whichever function is currently selected.
SCAN_MUX_GUARD_START = 5065
SCAN_MUX_GUARD_END = 5081

SCAN_PLAUSIBLE_MIN_C = -40.0
SCAN_PLAUSIBLE_MAX_C = 150.0
