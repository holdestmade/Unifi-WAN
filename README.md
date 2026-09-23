[![AI Assisted](https://img.shields.io/badge/AI--assisted-Claude-8A2BE2?logo=anthropic&logoColor=white)](#ai-disclosure)

# UniFi WAN

Home Assistant custom component

Pull WAN metrics from a UniFi OS console.

(UDM / UDR / UXG (with a separate cloud key) / UGW / EFG / UCG-Ultra etc.)


## AI Disclosure

This integration was developed with substantial assistance from AI (Anthropic's Claude).
Code was generated and iterated on through AI conversations, then reviewed, tested
and maintained by me on my own Home Assistant installation (HA 2026.x).


## Features

- Live WAN status, IP information and throughput sensors
- Speedtest automation with manual triggers and binary sensors for insight into the test lifecycle
- Speedtest server identification per WAN — which provider, host and location the test ran against
- ISP identification per WAN — provider, organisation, ASN and location, from the lookup the gateway performs against each WAN's own public address
- Redacted diagnostics for bug reports, and unredacted raw JSON dumps for looking at your own data

Primary UniFi Network API endpoints used:

- `GET /proxy/network/api/s/<site>/stat/device` — full site device stats (gateway, WAN sections, speedtest info)
- `GET /proxy/network/api/s/<site>/stat/device/<mac>` — lightweight per-gateway stats for fast WAN rates
- `POST /proxy/network/api/s/<site>/cmd/devmgr` — trigger a speedtest on the gateway
- `GET /proxy/network/v2/api/site/<site>/speedtest` — per-WAN speedtest records, where the controller supports it (older firmware answers 404 and is asked only once)
- `GET /proxy/network/api/s/<site>/rest/portforward` — the site's forwarding rules. Read only by the raw data dump, so what a console returns can be looked at; no sensor reads it and nothing is written back

Get the API key from your UniFi Console UI:

> **Settings → Control Plane → Integrations → API Keys**

This integration currently only supports UniFi OS consoles (UDM, UDR, UDM Pro, UXG, UCG, EFG etc.) using a local API key generated on the console, and the /proxy/network/api endpoints.
A console whose gateway model is newer than the list this integration knows is still recognised, by its reporting an uplink together with WAN interfaces.
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

These show the same figure as the active WAN's own per-WAN sensor, because both read the result the integration recorded for that WAN. That result is remembered, so a gateway that rewrites its last-run block between polls — some do, leaving it briefly empty — cannot drag these sensors back to an older run.

Two sources feed it — the controller's per-WAN record and the gateway's own last-run block — and the newer of the two that actually carries figures wins. Where the gateway names no interface for its run, the block is taken as the active WAN's unless a per-WAN record of the same moment shows it belonged to another line; some firmware names the interface only while a run is fresh, and without this the sensors would fall back to the previous result minutes later. On a multi-WAN gateway whose active uplink cannot be resolved at all, the newest result of any WAN is shown rather than nothing, and **UniFi Speedtest WAN Interface** names the WAN it describes.

Where the controller has no per-WAN records there is only one result to report, and these show it: the gateway’s `speedtest-status` block, falling back to the equivalent fields on the `uplink` section for firmware that does not report a block at all.

Those two are separate records of separate runs, so a result is taken whole from one of them and never assembled field by field. The gateway rewrites its block around a run and can be caught with a field missing or the whole block empty; borrowing the missing field from the `uplink` section paired the current run's throughput with the timestamp of whichever older run those legacy fields last caught — months earlier on firmware that no longer maintains them. A WAN's recorded result never moves backwards in time either, so a stale record cannot displace a newer one even for the one poll it takes to correct itself.

- **UniFi Speedtest Download**  
  - Gateway speedtest download result in **Mbit/s**  
- **UniFi Speedtest Upload**  
  - Gateway speedtest upload result in **Mbit/s**  
- **UniFi Speedtest Ping**  
  - Gateway speedtest latency in **ms**  
- **UniFi Speedtest Last Run**  
  - Timestamp of the last speedtest  
- **UniFi Speedtest Server Provider**  
  - Operator of the server the test ran against, e.g. `Exascale`  
- **UniFi Speedtest Server Provider URL**  
  - That operator's speedtest host  
- **UniFi Speedtest Server City**  
- **UniFi Speedtest Server Country**  

The last four describe **the far end of the test**, not your own line — the ISP sensors below are that. They are read only from the `server` sub-object of the gateway's `speedtest-status` block, never from the fields around it, which use some of the same names for the subscriber side.

**Per-WAN speedtest**

- **UniFi WAN\* Speedtest Download** (**Mbit/s**)  
- **UniFi WAN\* Speedtest Upload** (**Mbit/s**)  
- **UniFi WAN\* Speedtest Ping** (**ms**)  
- **UniFi WAN\* Speedtest Last Run** (timestamp)
- **UniFi WAN\* Speedtest Server Provider**
- **UniFi WAN\* Speedtest Server Provider URL**
- **UniFi WAN\* Speedtest Server City**
- **UniFi WAN\* Speedtest Server Country**

These are only created when the gateway has **more than one WAN**. With a single WAN they would just restate the gateway-wide **UniFi Speedtest** sensors above, since the one WAN is always the one tested.

Where these values come from depends on what the controller offers, and each sensor's `attributed_by` attribute records which route was used:

1. **`GET /proxy/network/v2/api/site/<site>/speedtest`** (`attributed_by: speedtest_api`) — newer controllers keep a speedtest record *per WAN*, each tagged with its own `wan_networkgroup`. When this is available every WAN shows its own genuine result, including WANs that are not the active uplink and tests started from the UniFi UI. No guesswork is involved. Some firmware does not add every run to that history, so a gateway result that is newer than a WAN's record and names that WAN's interface (`attributed_by: source_interface`) is used as well — without it a WAN whose record never moves would report a days-old figure while tests kept completing.
2. **The gateway's single global result**, attributed to one WAN — used only when the controller has no per-WAN API. The global result is overwritten by every run regardless of interface, so it is attributed on evidence: `speedtest-status.source_interface` (`attributed_by: source_interface`), else the WAN that is currently the active uplink (`attributed_by: active_wan`). The WAN a test was *requested* on is never used, because firmware that ignores the request always tests the active uplink and trusting it labels one line's throughput as another's.

On route 2 a WAN only accumulates results while it is the active uplink, and asking for a test on a non-active WAN updates the active WAN's sensors instead — the other WAN keeps its previous value rather than being given a figure that belongs to a different line. That case is logged as a warning, and the automatic speedtest stops cycling interfaces since every run would measure the same WAN. The same applies on route 1: a controller seen to record a run against a WAN other than the one asked for is not asked again.

Values survive Home Assistant restarts and only change when a speedtest actually runs on that WAN.

The **server** details are the exception to route 1 above: the per-WAN records name no server at all, so they come from the gateway's single `speedtest-status` block on either route. That block is overwritten by every run regardless of interface, so it is only claimed for a WAN on hard evidence — `source_interface`, or a gateway with a single WAN. The active uplink is *not* a fallback here as it is for throughput: a wrong guess at throughput is replaced by that WAN's next run, but the server latches and would sit there uncorrected. A multi-WAN gateway that names no interface therefore leaves the server sensors `unknown` rather than guessing, and a WAN keeps its last known server until a run it demonstrably owns replaces it.

**ISP identification**

- **UniFi WAN ISP** / **UniFi WAN\* ISP**  
  - Name of the internet provider the line runs over, e.g. `Kcom`  
- **UniFi WAN ISP Organization** / **UniFi WAN\* ISP Organization**  
  - The provider's registered organisation, where it differs from the trading name, e.g. `KCOM Group Limited`  
- **UniFi WAN ASN** / **UniFi WAN\* ASN**  
  - Autonomous system number the public address belongs to  
- **UniFi WAN City** / **UniFi WAN\* City**  
  - City the public address geolocates to  
- **UniFi WAN Country** / **UniFi WAN\* Country**  
  - Country the public address geolocates to  

These come from the geolocation lookup the gateway performs against each WAN's **own** public address, which it reports per WAN interface. Each WAN is therefore labelled with its own operator and can never inherit another line's; the gateway-wide sensors show whichever WAN is currently the active uplink, and report nothing while that cannot be resolved.

The lookup is independent of speedtests: it is refreshed on every poll and is populated for WANs that have never been speedtested. The per-WAN set is only created when the gateway has **more than one WAN**, since with a single WAN it would restate the gateway-wide set.

A gateway that performs no lookup — some firmware does not — leaves them `unknown`. The [diagnostics](#diagnostics) download shows what it reported, under `derived.geo_info`.

There is no separate ISP IP sensor: the public address these are derived from is already the **UniFi WAN IPv4** / **UniFi WAN\* IPv4** sensor above.

**Expected line speed**

Set what your line is sold as under **Options**, and the last speedtest is compared against it.

- **UniFi WAN ISP Expected Download Speed** (**Mbit/s**)
- **UniFi WAN ISP Expected Upload Speed** (**Mbit/s**)
  - Simply what you configured, exposed as a sensor so a dashboard can put the two figures side by side.
- **UniFi WAN ISP Download Speed Status**
- **UniFi WAN ISP Upload Speed Status**
  - `Expected`, `Faster` or `Slower`

The verdict allows a tolerance either way, 2% by default and configurable under **Options**. At 2%, against a 500 Mbit/s line:

| Last speedtest, against a 500 Mbit/s line | State |
| --- | --- |
| above 510 | `Faster` |
| 490 to 510 | `Expected` |
| below 490 | `Slower` |

The boundary counts as met — exactly 2% down is still the line delivering what it promised. A line is never sold as an exact number and a speedtest is not a precise instrument, so some band is needed. Widen it for a connection that varies by time of day, or set it to `0` so that only an exact match counts. Each sensor's `tolerance_percent` attribute reports the band it was judged by, so the state and the explanation cannot disagree.

Both sensors carry `expected_mbps`, `measured_mbps`, `difference_mbps` and `difference_percent` as attributes, so "by how much?" needs no template.

The comparison reads the same result the gateway-wide **UniFi Speedtest Download** / **Upload** sensors show, so the two can never disagree about what was measured. That means it follows the **active WAN** on a multi-WAN gateway; the expected figures are a single pair for the gateway, not one per line.

Leave either at `0` and its pair of sensors reports `unknown`. The sensors are always created, so filling the option in later does not change which entities exist.

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
  - A run counts as finished when **any** result the controller keeps moves — the gateway's own last-run block or any per-WAN record — because firmware differs over which of them a run updates, and a gateway that ignores the requested interface writes the result against a different WAN than the one asked for.
  - Where a gateway accepts a per-WAN request and then records nothing at all, the run is repeated as a plain whole-gateway speedtest and that gateway is not asked to target an interface again for the rest of the session. Both are logged as warnings naming the WAN and the interface involved.

---

### Service

- **`unifi_wan.run_speedtest`**
  - Triggers a one-off speedtest on the UniFi gateway  
  - Optional `wan` field selects a specific WAN interface (e.g. `2`); omit it to test the active WAN. With several gateways configured, only those that have that WAN are tested, and a WAN none of them has is refused with an error rather than sent to the console  
  - The test runs in the background; sensors refresh automatically once the controller reports a new result

- **`unifi_wan.dump_raw_data`**
  - Writes an **unredacted** JSON file of everything the controller returns to `config/unifi_wan_dumps/`, for your own inspection — see [Raw data dumps](#raw-data-dumps) below  
  - Optional `keep` field sets how many dumps to keep per gateway (default `10`)  
  - Returns the paths written, so calling it from **Developer tools → Actions** shows where the file landed

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
  - Keep this reasonably low frequency (e.g. 15–60s). The minimum is 5 seconds.  
  - UniFi API limit is ~100 calls per minute per API key.
- **Fast WAN rate interval (seconds)**  
  - Poll interval for the per-gateway endpoint: `stat/device/<mac>`  
  - Only fetches the gateway, so it’s much cheaper and is used for **live WAN up/down rates** (`UniFi WAN Download` / `UniFi WAN Upload`) and totals integration.  
  - Scan-interval WAN rate sensors (`UniFi WAN Download (Scan Interval)` / `UniFi WAN Upload (Scan Interval)`) continue updating on the Scan interval even when this is disabled.  
  - Set this to **0** to disable fast per-second polling entirely.  
  - You can set this to **1–2 seconds** for near real-time graphs when needed.  
  - This poll parses only as far as the two rate sensors read, so running it once a second costs a fraction of a full scan.

**Speedtest automation**

- **Run speedtest automatically** (on/off, default **on**)  
  - Enable/disable automatic speedtests entirely.
- **Auto speedtest interval (minutes)** (default **60**)  
  - How often to trigger an automatic speedtest when enabled.  
  - With more than one WAN interface, each run cycles to the next WAN that currently has link, so every WAN accumulates its own per-WAN speedtest results over time. With a single WAN the plain speedtest command is used.  
  - The rotation stops automatically if the gateway has no per-WAN speedtest API *and* is seen to ignore the requested interface, since every run would then measure the active uplink anyway.

**ISP line speed**

- **ISP Expected Download Speed** / **ISP Expected Upload Speed** (**Mbit/s**, default **0**)
  - What your line is sold as. Drives the **UniFi WAN ISP Download Speed Status** / **Upload Speed Status** sensors described above.
  - `0` means not configured: those sensors stay `unknown` rather than comparing against nothing.
- **ISP Speed Tolerance** (**%**, default **2**)
  - How far either side of the expected speed still counts as meeting it.
  - `0` means only an exact match reads `Expected`; the maximum is `50`.
- Changing any of these reloads the integration, as the other options here do.

---

## Changing the console address

**Settings → Devices & Services → UniFi WAN → ⋮ → Reconfigure**

Use this when the console moves to a new address or you change the site. It
keeps every entity and its history, and moves the integration's own identity
with it, so the same console cannot end up configured twice. The same fields
appear under **Options**, which now follows a change the same way.

Every request to the console times out after 30 seconds. A console that
accepts the connection and then stops answering therefore delays one poll,
rather than blocking it for the five minutes that was the underlying
library's default.

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

Credentials, MAC addresses, public IP addresses, serial numbers, account identifiers, DNS servers, the speedtest server's location and URL and the geolocation lookup behind the **WAN ISP/ASN/City** sensors are redacted. Three rules apply, in order:

1. **By field name** — an explicit list of the keys that carry them.
2. **By name shape** — any field whose name ends in `_id`, `_uuid`, `_token`, `_key`, `_authkey`, `_hash`, `_mac`, `_ip`, `_secret`, `_password` or `_fingerprint`.
3. **By value shape** — any value that *is* an IPv4 or IPv6 address, a MAC address, an email address or a long opaque hex identifier, whatever field it arrived in.

The third rule is the one that does not go stale. The controller gains fields with every firmware, so a list of names is always a release behind, and a field only turns out to be missing from it after someone has posted their diagnostics publicly. A new field carrying an address is redacted on sight, before anyone knows its name.

Fields the WAN logic turns on are deliberately kept: interface names, port numbers, up/enable flags, speedtest figures, timestamps, the WAN network group and the country name. Firmware versions are exempt from the value-shape rule, since `6.5.55.0` reads as a dotted quad but is exactly what a bug report needs. Redaction replaces values, not keys, and leaves nulls and blanks alone, so a diagnostics file still shows *whether* the controller populated each field.

Because addresses are hidden, whether two of them matched is reported in the `derived` section rather than left to be inferred. **Please attach this file when opening an issue.**

**Please Check the file for any data you do not want public before posting it publicly**

---

## Raw data dumps

For looking at your own data, redaction is only in the way — the hidden fields are usually the ones that explain the behaviour. The `unifi_wan.dump_raw_data` action writes the same information with **nothing removed**, to a file on the Home Assistant host.

**Developer tools → Actions → UniFi WAN: Dump raw data → Perform action**

Files are written to `config/unifi_wan_dumps/`, one per configured gateway, named `unifi_wan_<site>_<entry>_<YYYYmmdd-HHMMSS>.json`. The ten most recent per gateway are kept and older ones deleted; the `keep` field changes that. The action returns the paths it wrote, so the response pane in Developer tools tells you exactly where to look. Copy them off the host with the File editor / Samba / SSH add-on, or with `scp`.

The endpoints are fetched together rather than in turn, and the file is serialised on a worker thread, so a dump does not hold up polling. On a large site the file is mostly the device list; past 20 MB the log says so, names the file and reminds you how many are being kept, since these live in the config directory that gets backed up.

Each file contains:

- **`controller`** — every endpoint the integration reads, fetched fresh and captured verbatim, with the URL, HTTP status and content type alongside each body:
  - `stat_device` — the full site payload, **every device**, not just the gateway
  - `stat_device_gateway` — the cheap gateway-only endpoint behind the live rate sensors
  - `v2_speedtest` — the per-WAN speedtest history. Captured even when it fails: a `404` here is the answer to “why are my per-WAN speedtest sensors empty?”
  - `port_forwards` — the site's forwarding rules, including each rule's `pfwd_interface` (the WAN an inbound rule is bound to). Captured for the same reason: whether a local API key may read them at all, and how this firmware spells the interface, differ by console
- **`parsed`** — what the integration made of it: the WAN sections, `wan_alive`, `wan_status`, the normalised speedtest result, the per-WAN results and the `geo_info` blocks behind the ISP sensors. The device list is omitted (it is already in `stat_device` above, verbatim) and replaced by a `device_count`.
- **`parsed_rates`** — the same, from the fast per-gateway poll, when that interval is enabled
- **`derived`** — the conclusions: the resolved active WAN and how it was matched, the latched per-WAN speedtest results the sensors are showing, and whether the controller accepts targeted speedtests
- **`entry`** — your configured host, site and options

Nothing is uploaded and nothing is offered for download — the file has to be fetched off the host deliberately.

> ⚠️ **These files are unredacted.** They contain your public IP addresses, MAC addresses, serial numbers, site and device identifiers, DNS servers, the ISP/geolocation lookup for each WAN, and your forwarding rules — which internal host and port each one opens from outside. **Do not attach one to a GitHub issue or post it publicly** — use **Download diagnostics** for that, which redacts the rest and carries no forwarding rules at all. The only field held back is your API key, which is not controller data.

---

## Development

The integration is laid out so the parts that make decisions can be tested
without a running Home Assistant:

| Module | What it holds |
| --- | --- |
| `models.py` | The payload shapes and every parsing decision. Imports no Home Assistant at all. |
| `api.py` | The HTTP client and what it remembers about a console. |
| `coordinator.py` | The full poll and the fast rate poll. |
| `speedtest.py` | Triggering a run, waiting for it, and attributing the result to a WAN. |
| `runtime.py` | What one configured gateway carries, reached as `entry.runtime_data`. |

Run the tests with:

```bash
pip install -r requirements-test.txt
pytest
ruff check custom_components tests
```

GitHub Actions runs those plus `hassfest` and HACS validation on every push
and pull request.

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

---

## Licence

[MIT](LICENSE).

