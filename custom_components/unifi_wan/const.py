from __future__ import annotations

DOMAIN = "unifi_wan"

CONF_HOST = "host"
CONF_API_KEY = "api_key"
CONF_SITE = "site"
CONF_VERIFY_SSL = "verify_ssl"

CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30
LEGACY_CONF_DEVICE_INTERVAL = "device_interval"

CONF_RATE_INTERVAL = "rate_interval_seconds"
DEFAULT_RATE_INTERVAL = 5

CONF_MONTH_RESET_DAY = "month_reset_day"
DEFAULT_MONTH_RESET_DAY = 1

CONF_AUTO_SPEEDTEST = "auto_speedtest"
CONF_AUTO_SPEEDTEST_MINUTES = "auto_speedtest_minutes"
DEFAULT_AUTO_SPEEDTEST = True
DEFAULT_AUTO_SPEEDTEST_MINUTES = 60

DEFAULT_SITE = "default"
DEFAULT_VERIFY_SSL = False

PLATFORMS = ["sensor", "binary_sensor", "button", "switch"]

SIGNAL_SPEEDTEST_RUNNING = f"{DOMAIN}_speedtest_running"

GATEWAY_DEVICES = ["udm", "ugw", "uxg", "uxg-pro"]
