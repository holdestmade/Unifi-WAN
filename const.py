DOMAIN = "unifi_wan"

CONF_HOST = "host"
CONF_API_KEY = "api_key"
CONF_SITE = "site"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 30
LEGACY_CONF_DEVICE_INTERVAL = "device_interval"

CONF_AUTO_SPEEDTEST = "auto_speedtest"
CONF_AUTO_SPEEDTEST_MINUTES = "auto_speedtest_minutes"
DEFAULT_AUTO_SPEEDTEST = True
DEFAULT_AUTO_SPEEDTEST_MINUTES = 60

DEFAULT_SITE = "default"
DEFAULT_VERIFY_SSL = False

PLATFORMS = ["sensor", "binary_sensor", "button", "switch"]

SIGNAL_SPEEDTEST_RUNNING = f"{DOMAIN}_speedtest_running"
