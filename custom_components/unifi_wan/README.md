# UniFi WAN
Home Assistant custom component

Pull WAN metrics from a UniFi OS console (UDM/UDR/UXG) using the `X-API-Key` header — **single endpoint**: `/proxy/network/api/s/<site>/stat/device`.

Get API key from Unifi Console:
  settings/control-plane/integrations

Work in Progress

## Exposed entities
**Sensors**
- UniFi WAN IPv4 — from `gateway.uplink.ip`
- UniFi WAN IPv6 — from `gateway.uplink.ip6` (if present)
- UniFi WAN Download — `uplink.rx_bytes-r` → Mbit/s
- UniFi WAN Upload — `uplink.tx_bytes-r` → Mbit/s
- UniFi Speedtest Download — `uplink.xput_down` (Mbps)
- UniFi Speedtest Upload — `uplink.xput_up` (Mbps)
- UniFi Speedtest Ping — `uplink.speedtest_ping` (ms)
- UniFi Speedtest Last Run — `uplink.speedtest_lastrun` (timestamp)
- UniFi Active WAN Name — `uplink.name` (e.g., `ppp0`, `WAN`, `WAN2`)

**Binary sensors**
- UniFi WAN Internet — `uplink.up`
- UniFi Active WAN Up — same as above (kept for compatibility)
- UniFi WAN1 Link — `gateway.wan1.up`
- UniFi WAN2 Link — `gateway.wan2.up`

## Options
- **Host / IP**
- **API Key**
- **Site** (default `default`)
- **Verify SSL certificate**
- **Scan interval (s)** (Maximum API calls allowed is 100 per minute)
- **Run speedtest automatically** — on/off (default **on**)
- **Auto speedtest interval (minutes)** — default **60**

Changing any option revalidates against `/stat/device` and reloads the entry.

## Install
1. Copy `custom_components/unifi_wan/` into your Home Assistant `config` folder.
2. Restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → “UniFi WAN”.
4. Enter Host/IP, API Key, Site.

