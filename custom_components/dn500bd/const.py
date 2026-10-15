"""Constants for the Denon DN-500BD MKII integration."""

from __future__ import annotations

DOMAIN = "dn500bd"
MANUFACTURER = "Denon Professional"
MODEL = "DN-500BD MKII"

DEFAULT_PORT = 9030
DEFAULT_NAME = "DN-500BD"

# Options
CONF_POLL_PLAYING = "poll_playing"
CONF_POLL_IDLE = "poll_idle"
CONF_ACK_TIMEOUT = "ack_timeout_ms"
CONF_ANSWER_TIMEOUT = "answer_timeout_ms"
CONF_REVERSE_CODE = "reverse_code"
CONF_ACK_NOTIFICATIONS = "ack_notifications"

DEFAULT_POLL_PLAYING = 1.0  # seconds
DEFAULT_POLL_IDLE = 5.0  # seconds
DEFAULT_ACK_TIMEOUT = 300  # ms (protocol says 30 ms; HA/network jitter needs headroom)
DEFAULT_ANSWER_TIMEOUT = 500  # ms
DEFAULT_REVERSE_CODE = "R"  # undocumented, see README
DEFAULT_ACK_NOTIFICATIONS = True

# Media info (type, audio format, channels) is polled less often
MEDIA_INFO_INTERVAL = 30.0  # seconds

SERVICE_GOTO_CHAPTER = "goto_chapter"
SERVICE_GOTO_TITLE = "goto_title"
SERVICE_SCAN = "scan"
SERVICE_TRAY = "tray"
SERVICE_SEND_RAW = "send_raw"
