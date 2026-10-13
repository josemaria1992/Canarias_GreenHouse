"""The Modbus RTU error types, without pymodbus or pyserial, so the simulation can raise and catch them
(the twin's simulated drive, `twin/vfd.py`) without importing a serial stack. `rtu.py` re-exports them."""
from __future__ import annotations


class RtuError(Exception):
    """Base class. `kind` is a stable token stored with health and command outcomes."""
    kind = "RTU_ERROR"


class RtuNoResponse(RtuError):
    kind = "NO_RESPONSE"      # timeout, wrong unit (dropped by the framer), bad CRC, truncated frame


class RtuExceptionResponse(RtuError):
    kind = "EXCEPTION_RESPONSE"

    def __init__(self, fc: int, code: int):
        super().__init__(f"Modbus exception code {code} for function 0x{fc:02X}")
        self.fc, self.code = fc, code


class RtuInvalidResponse(RtuError):
    kind = "INVALID_RESPONSE"  # parsed, but wrong function code / unit / length / padding


class RtuRefused(RtuError):
    kind = "REFUSED"           # never sent: bad address, unsupported version, closed adapter
