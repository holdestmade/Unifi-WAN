# UniFi WAN
Home Assistant custom component

Pull WAN metrics from a UniFi OS console (UDM / UDR / UXG (with a separate cloud key) / UGW / etc.)

## Features

- Live WAN status, IP information and throughput sensors
- Speedtest automation with manual triggers and binary sensors for insight into the test lifecycle

Primary UniFi Network API endpoints used:

- `GET /proxy/network/api/s/<site>/stat/device` — full site device stats (gateway, WAN sections, speedtest info)
- `GET /proxy/network/api/s/<site>/stat/device/<mac>` — lightweight per-gateway stats for fast WAN rates
- `POST /proxy/network/api/s/<site>/cmd/devmgr` — trigger a speedtest on the gateway

Get the API key from your UniFi Console UI:

> **Settings → Control Plane → Integrations → API Keys**

This integration currently only supports UniFi OS consoles (UDM, UDR, UDM Pro, UXG, etc.) using a local API key generated on the console, and the /proxy/network/api endpoints.
It does not support:
- Standalone UniFi Network running in a VM or Docker without UniFi OS.
- API keys generated on unifi.ui.com for cloud-only access.
---

## Exposed entities

### Sensors

**WAN status & rates**

- **UniFi WAN1 IPv4**  
  - Current WAN1 IPv4 address  
- **UniFi WAN1 IPv6**  
  - Current WAN1 IPv6 address (if present)
- **UniFi WAN2 IPv4**  
  - Current WAN2 IPv4 address (if present) 
- **UniFi WAN2 IPv6**  
  - Current WAN2 IPv6 address (if present)  
- **UniFi WAN Download**  
  - Current downstream rate in **Mbit/s**  
- **UniFi WAN Upload**  
  - Current upstream rate in **Mbit/s**

**Speedtest**

Speedtest values are taken from the gateway’s `uplink` section after a speedtest completes.

- **UniFi Speedtest Download**  
  - Gateway speedtest download result in **Mbit/s**  
- **UniFi Speedtest Upload**  
  - Gateway speedtest upload result in **Mbit/s**  
- **UniFi Speedtest Ping**  
  - Gateway speedtest latency in **ms**  
- **UniFi Speedtest Last Run**  
  - Timestamp of the last speedtest  

**WAN identification**

- **UniFi Active WAN Name**  
  - Human-friendly description of the currently active WAN  
- **UniFi Active WAN ID**  
  - Logical ID of the active WAN: `WAN1`, `WAN2`, `WAN`, or `unknown`  
  - Heuristically derived from:
    - IP matches
    - Interface name matches
    - WAN section `up` flags
    - Legacy single-WAN `wan` section  
  - Attributes include the chosen section IP/interface and the match reason for debugging.

---

### Binary sensors

- **UniFi WAN1 Internet**
- **UniFi WAN2 Internet**  
- **UniFi Active WAN Up**  
- **UniFi WAN1 Link**  
- **UniFi WAN2 Link**  
- **UniFi Speedtest In Progress**  
  - `on` while an integration-triggered speedtest command is running  
  - Turns off once results have been pulled and sensors refreshed

---

### Switches

- **UniFi WAN Auto Speedtest**  
  - Enables/disables the integration’s scheduled speedtest job  
  - Toggling this switch updates the internal scheduler without needing to reconfigure the integration

---

### Buttons

- **Run UniFi Speedtest**
  - Triggers a one-off speedtest on the active UniFi gateway  
  - After a short delay, the integration refreshes `Speedtest` sensors with the latest results

---

### Service

- **Run UniFi Speedtest** 
  - Triggers a one-off speedtest on the active UniFi gateway  
  - After a short delay, the integration refreshes `Speedtest` sensors with the latest results

---

## Options

All options are available via the integration’s **Options** UI and can be changed later; changing any option:

- Revalidates the connection against `stat/device`
- Reloads the config entry cleanly

**Connection / API**

- **Host / IP**  
  - Your UniFi OS console address (e.g. `192.168.1.1` or `udm.local`)
- **API Key**  
  - X-API-Key generated in UniFi Console
- **Site**  
  - UniFi Network site name (default: `default`)
- **Verify SSL certificate**  
  - Enable to verify the console’s HTTPS certificate

**Polling / update intervals**

- **Scan interval (seconds)**  
  - How often to poll full `stat/device` for gateway, WAN sections, speedtest info, etc.  
  - This is the “heavier” call (all devices).  
  - Keep this reasonably low frequency (e.g. 15–60s).  
  - UniFi API limit is ~100 calls per minute per API key.
- **Fast WAN rate interval (seconds)**  
  - Poll interval for the per-gateway endpoint: `stat/device/<mac>`  
  - Only fetches the gateway, so it’s much cheaper and is used for **live WAN up/down rates** & **totals integration**.  
  - You can safely set this to **1–2 seconds** for near real-time graphs, as it is only a single device request.

**Speedtest automation**

- **Run speedtest automatically** (on/off, default **on**)  
  - Enable/disable automatic speedtests entirely.
- **Auto speedtest interval (minutes)** (default **60**)  
  - How often to trigger an automatic speedtest when enabled.

---

## Device information

The integration creates a single UniFi WAN **device** in Home Assistant with:

- Manufacturer
- Model
- Firmware
- MAC address
- Configuration URL

All sensors, binary sensors, buttons and switches are attached to this device so they show up on the same device card.

---

## Install

### HACS

1. Add this repository as a custom repository in HACS  
   `https://github.com/holdestmade/Unifi-WAN`
2. In Home Assistant, open **HACS → Integrations**, find **UniFi WAN** and install.
3. Restart Home Assistant if prompted.

### Manual

1. Copy the `custom_components/unifi_wan/` folder into your Home Assistant `config` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for **“UniFi WAN”**.
4. Enter:
   - Host/IP of your UniFi OS console  
   - API Key  
   - Site name (if not `default`)  
   - SSL verification preference

Once added, you’ll get a single UniFi WAN device with all the WAN, speedtest, and usage sensors attached.
