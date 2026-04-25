"""Raw protocol escape hatches for RE and advanced users.

Does not use the :class:`Command` spec machinery: the opcode is a
per-call parameter, not a compile-time constant. Instead, the caller
hands bytes to :meth:`Balance.raw_xbpi`, which routes through
:meth:`Session.execute_raw_xbpi`. That method applies a single hard
safety gate — the opcode must be on :data:`SAFE_READ_ONLY_OPCODES` or
the caller must pass ``confirm=True``. See design §6.1.

The SBI safe-list lives in :mod:`sartoriuslib.protocol.sbi.tables` because
SBI tokens are also used by the line codec and raw CLI.
"""

from __future__ import annotations

__all__ = ["SAFE_READ_ONLY_OPCODES"]


#: Opcodes the raw-xBPI escape hatch accepts without ``confirm=True``.
#:
#: All are :attr:`SafetyTier.READ_ONLY` or otherwise non-state-changing
#: per ``docs/protocol.md`` §7 (identity, weight, metrology, status,
#: tare-value read). Any opcode outside this set requires ``confirm=True``
#: because we can't know it's safe.
#:
#: This is deliberately conservative — it is the "you never need confirm
#: for these" baseline, not "everything else is dangerous." Safe opcodes
#: omitted here just need a confirm flag; nothing breaks.
SAFE_READ_ONLY_OPCODES: frozenset[int] = frozenset(
    {
        # Device information (§7.1)
        0x00,  # read_software_version
        0x01,  # read_factory_number
        0x02,  # read_weigh_cell_model
        0x03,  # read_user_id
        0x05,  # read_oem_text
        0x07,  # read_manufacturer
        0x0A,  # read_configuration_data
        0x0F,  # read_balance_info
        # Metrology (§7.2) — pure reads, TLV-wrapped on Cubis
        0x0B,  # read_threshold_0b
        0x0C,  # read_max
        0x0D,  # read_increment
        0x0E,  # read_threshold_0e
        # Weight reads (§7.4)
        0x1C,  # read_appl_tare
        0x1E,  # read_net_weight
        0x1F,  # read_net_weight_hires
        0x20,  # read_gross_weight
        0x21,  # read_gross_weight_hires
        0x22,  # read_tare
        0x23,  # read_tare_alias
        # Filter / mode config reads (§7.5)
        0x26,  # read_weighing_mode
        0x57,  # read_cycle_time
        # Status (§7.6)
        0x2E,  # read_slot_by_index (read-only sweep)
        0x2F,  # read_gross_bargraph
        0x30,  # read_balance_status_block
        0x32,  # read_balance_status
        0x35,  # read_time_stamp
        0x36,  # read_on_off_status
        # Temperature (§7.7)
        0x76,  # read_temperature_sensors
        # Parameter reads (§7.8)
        0x54,  # read_stop_flags
        0x55,  # read_parameter_table
        # Data interface reads (§7.10)
        0x71,  # read_sbn_address
        # Extended-opcode reads (§7.11)
        0xB9,  # read_last_cal_record
        0xBA,  # config_generation_counter
        0xBC,  # read_module_list
    }
)
