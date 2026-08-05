"""Presence detection.

Everything funnels into PresenceManager.trigger(). The backends differ only in
what physically pokes it: a GPIO pin, an mmWave module on a serial port, an
HTTP call from Home Assistant, or a finger on the touch screen.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

log = logging.getLogger(__name__)

# LD2410 report frames: header, footer, and the "no target" state byte.
LD2410_HEADER = b"\xf4\xf3\xf2\xf1"
LD2410_FOOTER = b"\xf8\xf7\xf6\xf5"


class PresenceManager:
    def __init__(self, config: dict) -> None:
        self.backend = str(config.get("backend", "http")).lower()
        self.hold_seconds = float(config.get("hold_seconds", 20))
        self._config = config
        self._last_seen = 0.0
        self._last_source = "none"
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def present(self) -> bool:
        if self.backend == "none":
            return True
        return (time.monotonic() - self._last_seen) < self.hold_seconds

    @property
    def last_source(self) -> str:
        return self._last_source

    def seconds_since_seen(self) -> float:
        if self._last_seen == 0.0:
            return float("inf")
        return time.monotonic() - self._last_seen

    def trigger(self, source: str = "http") -> None:
        self._last_seen = time.monotonic()
        self._last_source = source

    def trigger_threadsafe(self, source: str) -> None:
        """Called from sensor threads, which must not touch the event loop."""
        if self._loop is None:
            self.trigger(source)
            return
        self._loop.call_soon_threadsafe(self.trigger, source)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.backend == "gpio":
            self._start_thread(self._run_gpio, "presence-gpio")
        elif self.backend == "serial":
            self._start_thread(self._run_serial, "presence-serial")
        elif self.backend not in ("none", "http"):
            log.warning("unknown presence backend %r, falling back to http", self.backend)
            self.backend = "http"

    def _start_thread(self, target, name: str) -> None:
        self._thread = threading.Thread(target=target, name=name, daemon=True)
        self._thread.start()

    async def stop(self) -> None:
        self._stop.set()

    # --- backends -------------------------------------------------------

    def _run_gpio(self) -> None:
        try:
            from gpiozero import MotionSensor
        except ImportError:
            log.error(
                "presence backend 'gpio' needs gpiozero (pip install gpiozero lgpio); "
                "no motion will be detected"
            )
            return

        pin = int(self._config.get("gpio", {}).get("pin", 17))
        sensor = MotionSensor(pin)
        log.info("watching GPIO %d for motion", pin)
        while not self._stop.is_set():
            # wait_for_motion returns False on timeout, which is how we stay
            # responsive to shutdown instead of blocking forever.
            if sensor.wait_for_motion(timeout=1.0):
                self.trigger_threadsafe("gpio")
                sensor.wait_for_no_motion(timeout=1.0)

    def _run_serial(self) -> None:
        try:
            import serial
        except ImportError:
            log.error(
                "presence backend 'serial' needs pyserial (pip install pyserial); "
                "no motion will be detected"
            )
            return

        cfg = self._config.get("serial", {})
        port = cfg.get("port", "/dev/ttyUSB0")
        baud = int(cfg.get("baud", 256000))

        while not self._stop.is_set():
            try:
                with serial.Serial(port, baud, timeout=1.0) as conn:
                    log.info("reading LD2410 presence frames from %s", port)
                    self._read_ld2410(conn)
            except Exception as exc:
                log.warning("serial presence read failed (%s); retrying in 5s", exc)
                self._stop.wait(5.0)

    def _read_ld2410(self, conn) -> None:
        buffer = bytearray()
        while not self._stop.is_set():
            chunk = conn.read(64)
            if chunk:
                buffer.extend(chunk)
            # Keep the buffer from growing without bound if we're mid-resync.
            if len(buffer) > 4096:
                del buffer[:-1024]

            while True:
                start = buffer.find(LD2410_HEADER)
                if start < 0:
                    break
                if len(buffer) < start + 6:
                    break
                length = int.from_bytes(buffer[start + 4 : start + 6], "little")
                frame_end = start + 6 + length + len(LD2410_FOOTER)
                if len(buffer) < frame_end:
                    break
                payload = bytes(buffer[start + 6 : start + 6 + length])
                del buffer[:frame_end]
                # payload: [data type][0xAA][target state][...]
                if len(payload) >= 3 and payload[2] != 0x00:
                    self.trigger_threadsafe("mmwave")
