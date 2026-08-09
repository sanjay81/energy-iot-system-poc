# iot_device/buffer.py

import json
import os
import logging
import tempfile
import threading

logger = logging.getLogger(__name__)

# Maximum measurements to buffer
# At 1 measurement per 5 seconds:
# 17280 measurements = 24 hours
MAX_BUFFER_SIZE = 17280

# Alert threshold — 80% full
ALERT_THRESHOLD = 0.8

BUFFER_FILE = "iot_buffer.json"


class LocalBuffer:
    """
    Persistent local buffer for measurements during network outage.

    Stores measurements to a JSON file so data survives
    IoT device reboot during outage.

    Design decisions:
    - Circular buffer — when full, drop OLDEST data
    - Alert at 80% capacity — before overflow
    - Log exact timestamps of dropped measurements
    - On reconnect — upload in chronological order
    """

    def __init__(
        self,
        max_size: int = MAX_BUFFER_SIZE,
        buffer_file: str = BUFFER_FILE
    ):
        self.max_size = max_size
        self.buffer_file = buffer_file
        self._measurements = []
        self._dropped_count = 0
        self._lock = threading.RLock()
        self._load()

    def _load(self):
        """Load existing buffer from disk on startup."""
        if os.path.exists(self.buffer_file):
            try:
                with open(self.buffer_file, 'r') as f:
                    self._measurements = json.load(f)
                logger.info(
                    f"[Buffer] Loaded {len(self._measurements)} "
                    f"buffered measurements from disk"
                )
            except Exception as e:
                logger.error(f"[Buffer] Load failed: {e}")
                self._measurements = []

    def _save(self):
        """Persist the buffer atomically so a power loss cannot truncate it."""
        try:
            directory = os.path.dirname(os.path.abspath(self.buffer_file))
            os.makedirs(directory, exist_ok=True)
            fd, temporary_path = tempfile.mkstemp(dir=directory)
            try:
                with os.fdopen(fd, "w") as file_handle:
                    json.dump(self._measurements, file_handle)
                    file_handle.flush()
                    os.fsync(file_handle.fileno())
                os.replace(temporary_path, self.buffer_file)
            finally:
                if os.path.exists(temporary_path):
                    os.unlink(temporary_path)
        except Exception as e:
            logger.error(f"[Buffer] Save failed: {e}")

    @property
    def size(self) -> int:
        return len(self._measurements)

    @property
    def is_empty(self) -> bool:
        return len(self._measurements) == 0

    @property
    def fill_percentage(self) -> float:
        return len(self._measurements) / self.max_size

    def add(self, measurement: dict) -> bool:
        """
        Add measurement to buffer.
        Returns True if added successfully.
        Returns False if dropped due to overflow.
        """
        with self._lock:
            if self.fill_percentage >= ALERT_THRESHOLD:
                logger.warning(
                    f"[Buffer] WARNING — buffer at "
                    f"{self.fill_percentage*100:.0f}% capacity "
                    f"({self.size}/{self.max_size})"
                )

            if len(self._measurements) >= self.max_size:
                dropped = self._measurements.pop(0)
                self._dropped_count += 1
                logger.error(
                    f"[Buffer] OVERFLOW — dropped measurement "
                    f"from {dropped.get('timestamp', 'unknown')} "
                    f"Total dropped: {self._dropped_count}"
                )

            self._measurements.append(measurement)
            self._save()
        return True

    def get_all(self) -> list:
        """Return all buffered measurements in chronological order."""
        with self._lock:
            return list(self._measurements)

    def clear(self):
        """Clear buffer after successful upload."""
        with self._lock:
            count = len(self._measurements)
            self._measurements = []
            self._save()
        logger.info(
            f"[Buffer] Cleared {count} measurements "
            f"after successful upload"
        )

    def remove_uploaded(self, count: int):
        """
        Remove first N measurements after upload.
        Safer than clear() — only removes confirmed uploaded.
        """
        with self._lock:
            self._measurements = self._measurements[count:]
            self._save()

    @property
    def dropped_count(self) -> int:
        return self._dropped_count
