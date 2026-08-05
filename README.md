# UniFi WAN
Home Assistant custom component

Pull WAN metrics from a UniFi OS console (UDM / UDR / UXG (with a separate cloud key) / UGW / EFG / UGC-Ultra etc.)

## Features

- Live WAN status, IP information and throughput sensors
- Speedtest automation with manual triggers and binary sensors for insight into the test lifecycle

Primary UniFi Network API endpoints used:

- `GET /proxy/network/api/s/<site>/stat/device` — full site device stats (gateway, WAN sections, speedtest info)
- `GET /proxy/network/api/s/<site>/stat/device/<mac>` — lightweight per-gateway stats for fast WAN rates
- `POST /proxy/network/api/s/<site>/cmd/devmgr` — trigger a speedtest on the gateway
- `GET /proxy/network/v2/api/site/<site>/speedtest` — per-WAN speedtest records, where the controller supports it (older firmware answers 404 and is asked only once)

Get the API key from your UniFi Console UI:

> **Settings → Control Plane → Integrations → API Keys**

This integration currently only supports UniFi OS consoles (UDM, UDR, UDM Pro, UXG, UFG etc.) using a local API key generated on the console, and the /proxy/network/api endpoints.
It does not support:
- Standalone UniFi Network running in a VM or Docker without UniFi OS.
- API keys generated on unifi.ui.com for cloud-only access.
---

## Exposed entities

### Sensors

**WAN status & rates**

- **UniFi WAN\* IPv4**  
  - Current WAN\* IPv4 address  
- **UniFi WAN\* IPv6**  
  - Current WAN\* IPv6 address (if present)
- **UniFi WAN Download**  
  - Current downstream rate in **Mbit/s**  
  - Updates on the **Fast WAN rate interval**
- **UniFi WAN Upload**  
  - Current upstream rate in **Mbit/s**  
  - Updates on the **Fast WAN rate interval**
- **UniFi WAN Download (Scan Interval)**  
  - Current downstream rate in **Mbit/s**  
  - Updates on the **Scan interval**
- **UniFi WAN Upload (Scan Interval)**  
  - Current upstream rate in **Mbit/s**  
  - Updates on the **Scan interval**

*\*One for each available WAN interface. These per-WAN entities are only created when the gateway has **more than one WAN** — with a single WAN they would restate the gateway-wide entities above.*

**Speedtest**

These report the **active WAN's** speedtest result, so on a multi-WAN gateway they always match the per-WAN sensors of whichever WAN is currently the uplink. Testing a non-active WAN updates that WAN's own sensors and leaves these alone.

Where the controller has no per-WAN records there is only one result to report, and these show it: the gateway’s `speedtest-status` block, falling back to the equivalent fields on the `uplink` section for firmware that does not report it.

- **UniFi Speedtest Download**  
  - Gateway speedtest download result in **Mbit/s**  
- **UniFi Speedtest Upload**  
  - Gateway speedtest upload result in **Mbit/s**  
- **UniFi Speedtest Ping**  
  - Gateway speedtest latency in **ms**  
- **UniFi Speedtest Last Run**  
  - Timestamp of the last speedtest  

**Per-WAN speedtest**

- **UniFi WAN\* Speedtest Download** (**Mbit/s**)  
- **UniFi WAN\* Speedtest Upload** (**Mbit/s**)  
- **UniFi WAN\* Speedtest Ping** (**ms**)  
- **UniFi WAN\* Speedtest Last Run** (timestamp)

These are only created when the gateway has **more than one WAN**. With a single WAN they would just restate the gateway-wide **UniFi Speedtest** sensors above, since the one WAN is always the one tested.

Where these values come from depends on what the controller offers, and each sensor's `attributed_by` attribute records which route was used:

1. **`GET /proxy/network/v2/api/site/<site>/speedtest`** (`attributed_by: speedtest_api`) — newer controllers keep a speedtest record *per WAN*, each tagged with its own `wan_networkgroup`. When this is available every WAN shows its own genuine result, including WANs that are not the active uplink and tests started from the UniFi UI. No guesswork is involved.
2. **The gateway's single global result**, attributed to one WAN — used only when the controller has no per-WAN API. The global result is overwritten by every run regardless of interface, so it is attributed on evidence: `speedtest-status.source_interface` (`attributed_by: source_interface`), else the WAN that is currently the active uplink (`attributed_by: active_wan`). The WAN a test was *requested* on is never used, because firmware that ignores the request always tests the active uplink and trusting it labels one line's throughput as another's.

On route 2 a WAN only accumulates results while it is the active uplink, and asking for a test on a non-active WAN updates the active WAN's sensors instead — the other WAN keeps its previous value rather than being given a figure that belongs to a different line. That case is logged as a warning, and the automatic speedtest stops cycling interfaces since every run would measure the same WAN.

Values survive Home Assistant restarts and only change when a speedtest actually runs on that WAN.

**WAN identification**

- **UniFi Active WAN ID**  
  - Logical ID of the active WAN (e.g. `WAN1`), or `Unknown`  
  - Derived by matching the uplink IP against each WAN section, then the uplink interface name against each WAN section's, then falling back to the only WAN that is up  
  - Attributes include the resolved WAN, the match reason and the per-WAN IPs, interface names and ports for debugging
- **UniFi Active WAN Name**  
  - Human-friendly description of the currently active WAN, e.g. `Virgin Fibre (Port 9)` or `WAN1 (Port 9)` when the controller has no description of its own  
  - Always derived from the same WAN section as **UniFi Active WAN ID**, so the two sensors can never point at different interfaces  
  - The WAN is qualified by its **chassis port** rather than its raw kernel interface name. UniFi numbers interfaces from zero and ports from one, so `eth8` is the port labelled **9** on the case — reporting the interface name directly reads like the neighbouring port. The port is only ever taken from the controller's own `physical_ports`/`port_table` data; where that is unavailable the interface name is shown instead, and it is never converted by arithmetic.

---

### Binary sensors

- **UniFi WAN\* Internet**
- **UniFi Active WAN Up**  
- **UniFi WAN\* Link**  
- **UniFi Speedtest In Progress**  
  - `on` while an integration-triggered speedtest command is running  
  - Turns off once results have been pulled and sensors refreshed

*\*One for each available WAN interface. These per-WAN entities are only created when the gateway has **more than one WAN** — with a single WAN they would restate the gateway-wide entities above.*

---

### Switches

- **UniFi WAN Auto Speedtest**  
  - Enables/disables the integration’s scheduled speedtest job  
  - Toggling this switch is saved to the integration options, so it stays in sync with the **Run speedtest automatically** option and survives restarts

---

### Buttons

- **Run UniFi Speedtest**
  - Triggers a one-off speedtest on the active UniFi gateway (plus one button per WAN interface, where the gateway has more than one WAN)  
  - The test runs in the background; the integration polls the controller until a new result is reported (up to 5 minutes) and then refreshes the `Speedtest` sensors. `UniFi Speedtest In Progress` stays `on` while it waits.

---

### Service

- **`unifi_wan.run_speedtest`**
  - Triggers a one-off speedtest on the UniFi gateway  
  - Optional `wan` field selects a specific WAN interface (e.g. `2`); omit it to test the active WAN  
  - The test runs in the background; sensors refresh automatically once the controller reports a new result

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
  - Only fetches the gateway, so it’s much cheaper and is used for **live WAN up/down rates** (`UniFi WAN Download` / `UniFi WAN Upload`) and totals integration.  
  - Scan-interval WAN rate sensors (`UniFi WAN Download (Scan Interval)` / `UniFi WAN Upload (Scan Interval)`) continue updating on the Scan interval even when this is disabled.  
  - Set this to **0** to disable fast per-second polling entirely.  
  - You can set this to **1–2 seconds** for near real-time graphs when needed.

**Speedtest automation**

- **Run speedtest automatically** (on/off, default **on**)  
  - Enable/disable automatic speedtests entirely.
- **Auto speedtest interval (minutes)** (default **60**)  
  - How often to trigger an automatic speedtest when enabled.  
  - With more than one WAN interface, each run cycles to the next WAN that currently has link, so every WAN accumulates its own per-WAN speedtest results over time. With a single WAN the plain speedtest command is used.  
  - The rotation stops automatically if the gateway has no per-WAN speedtest API *and* is seen to ignore the requested interface, since every run would then measure the active uplink anyway.

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

## Diagnostics

**Settings → Devices & Services → UniFi WAN → ⋮ → Download diagnostics**

Produces a JSON file containing what the controller sent and what the integration made of it — the fastest way to get a bug report answered, and it needs no logger configuration:

- The gateway's payload verbatim: the WAN sections, `uplink`, `speedtest-status`, `port_table` and `last_wan_interfaces`
- The per-WAN speedtest API's raw response, where the controller offers one
- The integration's own conclusions: the resolved active WAN and how it was matched, the parsed per-WAN results, and the values the sensors are currently showing

Credentials, MAC addresses, public IP addresses, serial numbers, account identifiers, DNS servers and the speedtest server's location are redacted — both by an explicit list of field names and by shape, so that any field whose name ends in `_id`, `_uuid`, `_token`, `_key`, `_authkey`, `_hash`, `_mac`, `_ip`, `_secret`, `_password` or `_fingerprint` is redacted even if a firmware update introduced it and nobody has seen it before.

Because addresses are hidden, whether two of them matched is reported in the `derived` section rather than left to be inferred. **Please attach this file when opening an issue.**

**Please Check the file for any data you do not want public before posting it publicly**

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
