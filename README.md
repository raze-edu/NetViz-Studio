# NetViz Studio v2.1

Offline-first, cross-platform **Logical & Physical Network CAD**, **Geo-Referenced Multi-Floor Editor**, and **Wi-Fi Field Survey Suite** uniting:

1. **Windows Desktop Studio (`desktop/`, Python 3.11/3.12 + PySide6)**
2. **Native Android Field Survey Companion (`android_app/`, Kotlin + Jetpack Compose + Material 3 — Zero Docker)**
3. **Shared Pure-Python Domain & Math Core (`shared/`, schema `v2.1.0` with `v2.0.0` backward compatibility)**

---

## Table of Contents

1. [Windows Desktop Installation & CLI Flags](#1-windows-desktop-installation--cli-flags)
2. [Component Templates & Bulk Port Generator](#2-component-templates--bulk-port-generator)
3. [Tab 1 — Port-to-Port Drag & Click Connecting, Translucent Nodes & Auto-Layout](#3-tab-1--port-to-port-drag--click-connecting-translucent-nodes--auto-layout)
4. [Tab 2 (Part A) — Google Maps API Key, Referrer Setup & Offline Leaflet Fallback](#4-tab-2-part-a--google-maps-api-key-referrer-setup--offline-leaflet-fallback)
5. [Tab 2 (Part A) — Placing, Tracing & Rotating Buildings on the Map](#5-tab-2-part-a--placing-tracing--rotating-buildings-on-the-map)
6. [Tab 2 (Part B) — Floor Editor View Rotation, Auto-Cut Rooms, Blueprints & Wall-Locked Doors](#6-tab-2-part-b--floor-editor-view-rotation-auto-cut-rooms-blueprints--wall-locked-doors)
7. [Tab 3 — Physical Mapping, Inter-Floor Risers, Cable BOM & Pre-Survey Coverage](#7-tab-3--physical-mapping-inter-floor-risers-cable-bom--pre-survey-coverage)
8. [Tab 4 — Wi-Fi Survey Analysis, Clipped Heatmaps & PNG/SVG/PDF Export](#8-tab-4--wi-fi-survey-analysis-clipped-heatmaps--pngsvgpdf-export)
9. [Zero-Docker Native Android APK Build (`build_apk.ps1` & CI)](#9-zero-docker-native-android-apk-build-build_apkps1--ci)
10. [LAN Sync, Windows Firewall Troubleshooting & USB ADB Reverse Sync](#10-lan-sync-windows-firewall-troubleshooting--usb-adb-reverse-sync)
11. [Android GPS Calibration, Diagnostics, Offline Map Backdrop & Wi-Fi Scan Throttling](#11-android-gps-calibration-diagnostics-offline-map-backdrop--wi-fi-scan-throttling)
12. [Global Shortcuts, Validation & Automated Verification](#12-global-shortcuts-validation--automated-verification)

---

## 1. Windows Desktop Installation & CLI Flags

### Prerequisites

- Windows 10 or Windows 11 (x64)
- Python `3.11` or `3.12`

### Setup Commands (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-desktop.txt
python scripts/fetch_vendor_assets.py
```

> **Note on `PySide6-Addons==6.8.1`**: `requirements-desktop.txt` pins `PySide6-Addons==6.8.1` (instead of a separate `PySide6-WebEngine` package, which does not exist on PyPI) because `PySide6-Addons` provides `QtWebEngineWidgets` and `QtWebChannel`.
>
> `scripts/fetch_vendor_assets.py` downloads and SHA-256 verifies **Leaflet 1.9.4** (`leaflet.js` & `leaflet.css`) into `desktop/ui/map_tab/map_assets/vendor/`. If offline during airgapped tests, `map_controller.js` also falls back to a built-in HTML5 `<canvas>` tile grid.

### Launching Desktop Studio & CLI Flags

```powershell
python -m desktop.main
```

Supported CLI flags (`desktop/main.py`):

| Flag | Description |
| --- | --- |
| `--project <path>` | Opens a `.netviz` archive immediately on startup (skips crash recovery prompt). |
| `--offline` | Runs the local `TileCacheServer` strictly offline using cached `tiles.sqlite` tiles (missing tiles render a `256×256` dark slate `"Offline"` grid PNG). |
| `--debug-log` | Sets log verbosity to `DEBUG` for both console output and the rotating log file. |

Local persistence paths under `%APPDATA%\NetVizStudio\` (overridable via `NETVIZ_CONFIG_DIR` for isolated testing):

- `config.json` — Local preferences, Google Maps API key, node opacity alpha, and recent files
- `templates.json` — Global custom component templates
- `autosave/recovery.netviz` — Periodic 120-second autosave snapshot (offered on startup if present)
- `logs/netviz.log` — Rotating log file (`5 MB` × `3` backups)

Explore immediately without drawing from scratch via **File → Load Sample Campus Project** (or the **Sample Campus** toolbar button), which loads a Berlin campus (`52.520000, 13.405000`) with 2 geo-anchored buildings, multi-floor rooms/doors/stairs/risers, all 9 device types, VLAN-tagged cables, and a 25-sample multi-SSID Wi-Fi survey.

---

## 2. Component Templates & Bulk Port Generator

### 9 Built-In Component Presets (`shared/presets.py`)

| Type (`ComponentType`) | Prefix | Accent | Default Ports & Specs |
| --- | --- | --- | --- |
| Passive Switch (`passive_switch`) | `PSW` | `#64748B` | 5× `Base1000` (`P1`–`P5`, 1000 Mbps) |
| Active Switch (`active_switch`) | `ASW` | `#38BDF8` | 24× `Base1000` (`G1`–`G24`, PoE) + 2× `SFP_PLUS` (`SFP+1`, `SFP+2`, 10G) |
| Access Point (`access_point`) | `AP` | `#22C55E` | 1× `Base1000` (`eth0`, PoE), `2.4 / 5.0 GHz`, `["Corp-WiFi"]` |
| Firewall (`firewall`) | `FW` | `#EF4444` | 4× `Base1000` (`WAN`, `LAN`, `DMZ`, `MGMT`) + 2× `SFP` (`SFP1`, `SFP2`) |
| Router (`router`) | `RTR` | `#F59E0B` | 1× `Base2500` (`WAN`, 2500 Mbps) + 4× `Base1000` (`LAN1`–`LAN4`) |
| LWL Converter (`lwl_converter`) | `LWL` | `#FACC15` | 1× `Base1000` (`RJ45`) + 1× `LWL_SM` (`LC-SM`, 10G) |
| PowerLAN Adapter (`powerlan_adapter`) | `PLC` | `#10B981` | 2× `Base1000` (`ETH1`, `ETH2`) + 1× `PowerLAN` (`PWR`, 1200 Mbps) |
| Patch Panel (`patch_panel`) | `PP` | `#94A3B8` | 24× `Base1000` (`PP-01`–`PP-24`) |
| End Device (`device`) | `DEV` | `#A855F7` | 1× `Base1000` (`eth0`) |

### Custom Templates, Port Blocks & `.netviz-templates.json`

- **Component Palette (Tab 1 left pane)**: Collapsible **Built-In Types (9)** and **My Custom Templates** sections with live search, drag-to-canvas or double-click spawn, and **`+ New Template`**.
- **Template Manager (`Tools → Template Manager...`)**: Create, edit, clone, and delete templates stored globally (`%APPDATA%\NetVizStudio\templates.json`) or inside the current project (`Project.custom_templates`).
- **Bulk Port-Block Generator**: Generate numbered port ranges (`Label Prefix`, `Start Number`, `Port Count`, `Zero-Pad Digits`, `PortType`, `Speed Mbps`, `PoE Capable`) in one action inside both `TemplateManagerDialog` and `ComponentInspector`.
- **Save Component as Template**: Convert any configured canvas device into a reusable template directly from `ComponentInspector`.
- **Import / Export**: Share template libraries as portable `.netviz-templates.json` files (`shared/bundle_io.py`).

---

## 3. Tab 1 — Port-to-Port Drag & Click Connecting, Translucent Nodes & Auto-Layout

### Dual-Mode Bulletproof Port Connecting (`LogicalCanvasView`)

All port connection mouse events are owned centrally by `LogicalCanvasView` (with scale-aware `find_port_at_scene_pos(..., hit_radius_screen_px=18.0)` and hit-test-excluded preview graphics), ensuring clicking a port pin or label never drags the parent component:

- **Drag-to-Connect**: Press on a free port pin or label, drag toward a target port (snapping cleanly with a glowing green lock ring within `18 px` screen radius, or a red `X` ring if occupied/self), and release to commit.
- **Click-to-Click Connect**: Click a free port A (`<= 6 px` movement) to enter `PENDING_CLICK_CONNECT`, move to target port B, and click to commit (`Esc` cancels).
- **Inspector Connect / Patch Table**: Use the interactive **Port Patch Table** (`Label | Type | Speed / PoE | Connected To | Action`) in `ComponentInspector` to connect/disconnect ports or click **Connected To** to jump to and pulse the remote device.
- **Media Mismatch Feedback**: Connecting mismatched media families (e.g., `LWL_SM` ↔ `Base1000`) still commits the cable while raising a non-blocking amber toast and flagging `PORT_MEDIA_MISMATCH` in the Validation Panel.

### Translucent Nodes, Visual Z-Stack & Trace

- **Node Opacity Slider**: Adjusts `ComponentNodeItem` fill alpha (`77–242`, i.e., `30%–95%`, default `165` ≈ `65%`) live and persists to `config.json`.
- **Strict Z-Order**: Background grid (`-100`) → Cables & Bundles (`10`) → Translucent Node Bodies (`20`) → Port Pins & Labels (`40`) → Cable Endpoint Port Pills (`50`) → Hovered/Selected Trace (`60`) → Preview Wire & Lock Ring (`65`) → Waypoint/Bundle Handles & Alignment Guides (`70`).
- **Hover Trace & Connection Trace Bar**: Hovering a port, cable, or bundle dims unrelated items to `0.30` opacity, lifts connected cables above translucent nodes, and updates the bottom **Connection Trace Bar**.

### Waypoints, Multi-Cable Bundling & Auto-Layout

- **Waypoints & Orthogonal Routing**: Double-click a cable segment to insert a draggable `WaypointHandle` (right-click handle to delete), or click **Auto-Orthogonal** for clean Manhattan routing.
- **Multi-Cable Bundling**: Select ≥ 2 cables and click **Bundle** (`collapsed=True` draws a thick trunk with `"<name> (N)"` pill and tooltip; `collapsed=False` renders parallel member wires offset by `(i - (N - 1)/2) * 5.0 px` with non-crossing fan-outs).
- **Align / Distribute & Auto-Layout Hierarchy**: Align (`Left/Center/Right/Top/Middle/Bottom`), Distribute (`H / V`), Smart Duplicate (`Ctrl+C`/`Ctrl+V`, auto-incrementing name and clearing duplicate MAC), and 1-click **Auto-Layout Hierarchy** (4-tier layout with barycenter crossing minimization).

---

## 4. Tab 2 (Part A) — Google Maps API Key, Referrer Setup & Offline Leaflet Fallback

### Local HTTP Serving & Security

`TileCacheServer` (`desktop/tile_server.py`) serves `desktop/ui/map_tab/map_assets/map.html` over `http://127.0.0.1:<port>/assets/map.html` with a `QWebChannel` `on_map_ready()` handshake and JS console error forwarding—avoiding `file:///` CORS and HTTP Referrer failures.

- Your **Google Maps API Key** is stored **strictly locally** in `%APPDATA%\NetVizStudio\config.json` (or fallback environment variable `NETVIZ_GOOGLE_MAPS_API_KEY`) and is **NEVER** written into `.netviz` project archives.

### 3 Entry Points to Set Your Google Maps API Key

1. **Tab 2 Map Toolbar**: Click **`🔑 Map API Key`** (focuses straight onto the API Key field).
2. **Main Toolbar**: Click **`⚙ Settings`**.
3. **Menu**: **File → Settings... (`Ctrl+,`)**.

### Google Cloud Console Configuration

1. Enable **Maps JavaScript API** (required for interactive map tiles and satellite/roadmap layers).
2. Optionally enable **Geocoding API** (used by the Location Jump Bar for address lookup; falls back to Nominatim when no Google key is set).
3. Under **API Key → Application restrictions (HTTP referrers)**, either:
   - Add `127.0.0.1/*` (recommended for local desktop serving), **or**
   - Leave HTTP referrers unrestricted.
4. Click **Save & Reload Map** in `SettingsDialog` to reload Tab 2 live without restarting NetViz Studio.

### Automatic Offline & Fallback Engines

- **Preferred Provider**: Choose *Google Maps (Primary + Leaflet Fallback)*, *Leaflet Satellite (Free)*, or *Leaflet OpenStreetMap (Free)*.
- If Google Maps has no key, fails auth (`gm_authFailure` / `RefererNotAllowedMapError` surfaced in Tab 2's banner), times out (`4000 ms`), or `--offline` is active, `map_controller.js` automatically switches to bundled **Leaflet 1.9.4** backed by `/tiles/<provider>/<z>/<x>/<y>` (`osm` and `esri_satellite`) and local `tiles.sqlite`.

---

## 5. Tab 2 (Part A) — Placing, Tracing & Rotating Buildings on the Map

- **Trace New Building**: Click **Trace Building** on the map toolbar, click polygon vertices on Google Maps or Leaflet, and double-click/finish. Python computes the centroid `GeoAnchor(origin_lat, origin_lon, rotation_deg=0.0)` and initializes `Floor 0` (`outer_polygon_m`) via `polygon_latlon_to_local_m()`.
- **Manual & Unplaced Buildings**: Click **+ Manual Building** to create an unplaced building (`geo_anchor = None`), then click **Place on Map** and click any map coordinate to anchor it.
- **Move & Rotate Handles**: Selecting an anchored building displays a center **Move Handle** (`origin_lat`, `origin_lon`) and an outer **Rotate Handle** updating `GeoAnchor.rotation_deg` (**clockwise degrees from Geographic True North**).
- **Single Source of Truth Invariant**: `Floor 0` (`outer_polygon_m`) + `Building.geo_anchor` is the single source of truth; whenever either changes (on the map or in the floor editor), `Building.footprint_latlon` is deterministically recomputed via `floor_polygon_to_latlon()`.
- **Location Jump Bar**: Enter `lat, lon` offline (e.g. `52.5200, 13.4050`) or search an address online.
- **Offline Tile Prefetcher**: Click **Cache Map Tiles** to download `osm` and `esri_satellite` tiles covering all anchored buildings plus `offline_cache_margin_m` (`50–2000 m`, default `250 m`) across `offline_cache_zooms` (`15–20`, defaults `16–19`) into `tiles.sqlite` (packed inside `.netviz`).

---

## 6. Tab 2 (Part B) — Floor Editor View Rotation, Auto-Cut Rooms, Blueprints & Wall-Locked Doors

### Strict Non-Panning Tool State Machine

Left-clicking in drawing or selection tools **never** accidentally pans. Viewport panning occurs **only** via **Middle-Mouse Drag**, **`Space` + Left-Drag**, or the explicit **Pan Tool (`H`)**:

| Tool | Shortcut | Action |
| --- | --- | --- |
| Select | `V` | Select/drag Room corner & midpoint `+` handles, wall-locked Doors, Stairs, Holes, or Rooms |
| Edit Outline | `E` | Reshape `Floor.outer_polygon_m` (drag corners, click `+` midpoint to add, right-click corner to delete) |
| Draw Room | `R` | Click vertices (`Backspace` undoes last vertex; `Enter`/start-ring/double-click finishes) |
| Place Door | `D` | Place wall-locked Door on floor outer wall or room wall (blocked in open space) |
| Draw Stairs | `S` | Press-drag-release staircase polygon with tread hatch & direction arrow |
| Place Riser Hole | `O` | Place vertical riser hole (snaps within `0.4 m` to adjacent-floor holes and auto-shares `riser_group_id`) |
| Align View to Edge | `A` | Click any floor/room wall segment to rotate the canvas view so that wall is horizontal (`0°`) |
| Calibrate Blueprint | `B` | Click 2 reference points on a loaded blueprint image and enter real-world meters |
| Pan | `H` | Left-drag pans the viewport |

### View-Only Rotation & True North Compass

- Rotate the floor canvas via `-90°`, `-15°`, spinbox (`-180°` to `180°`), `+15°`, `+90°`, `Reset 0°`, or **`Align View to Edge (A)`**.
- Updating `Floor.editor_view_rotation_deg` is strictly view-only and **never** mutates `Building.geo_anchor.rotation_deg` or stored coordinates.
- The top-right **True North Compass (`N ↑`)** renders at `(floor.editor_view_rotation_deg - anchor.rotation_deg) % 360`.

### Auto-Cut Rooms, Typed Wall Lengths, Doors & Blueprints

- **Safe Double-Click Finishing**: Deduplicates clicks `< 6 px` apart and guards for `250 ms` post-finish so finishing a room never inserts a zero-length edge or drags the new room.
- **Typed Exact Metric Wall Lengths**: While drawing a room (`DRAW_ROOM`), type a number (e.g. `4.5`) and press `Enter` to place the next vertex at that exact meter distance along the current cursor angle (`Shift` locks `0° / 45° / 90°` relative to view rotation).
- **Magnetic Snapping & Shapely Auto-Cut**: Snaps in priority order: vertex (`0.35 m`) → wall (`0.35 m`) → H/V align (`0.25 m`) → `0.25 m` grid. With **Auto-Cut Overlapping Walls** and **Clip to Floor Outline** enabled (default ON), new and reshaped rooms cut flush against neighboring rooms and clip to `outer_polygon_m`.
- **Wall-Locked Movable Doors**: Dragging a door in `SELECT (V)` projects it smoothly along walls via `project_point_to_walls()` without detaching. Press **`Space`** (or use the Inspector) to flip swing, flip hinge, or adjust width (`0.60–3.00 m`).
- **Onion-Skinning (`Shift+O`)**: Overlays `level_index - 1` at `25%` opacity for aligning multi-floor walls and vertical risers.
- **Blueprint Image Underlay**: Load PNG/JPG per floor (packed inside `.netviz` under `blueprints/<floor_id>.png`), adjust opacity/offset, and calibrate exact `meters_per_pixel` using **`CALIBRATE_BLUEPRINT (B)`**.

---

## 7. Tab 3 — Physical Mapping, Inter-Floor Risers, Cable BOM & Pre-Survey Coverage

- **Live-Linked Placement**: Drag or double-click unplaced logical devices from the left sidebar onto any building floor plan. Moves and deletions stay live-synced with Tab 1 and `QUndoStack`.
- **Automatic Physical Cables & Riser Jumps**: Cables whose endpoints are placed on the same floor render automatically with editable waypoints and bundling. Inter-floor cables route through the nearest matching `VerticalHole.riser_group_id` pair and display clickable jump badges (double-click jumps directly to the peer floor).
- **Live Metric Cable BOM Panel**: Computes horizontal run length + vertical riser height (`abs(level_a - level_b) * building.floor_height_m`) + configurable slack % (default `10%`), grouped by `PortType` and per cable run.
- **Pre-Survey Wi-Fi Coverage Preview**: Toggle **Pre-Survey Wi-Fi** to visualize an analytical log-distance path-loss estimate (`RSSI = -35 - 20*log10(d) - wall_crossings * room.wall_attenuation_db`) around placed Access Points before performing an on-site walk.

---

## 8. Tab 4 — Wi-Fi Survey Analysis, Clipped Heatmaps & PNG/SVG/PDF Export

- **Multi-Session Import & Merge**: Import `.netviz-survey.json` or `.netviz` files (or receive surveys live via LAN Sync), and check multiple sessions to merge them seamlessly.
- **Filters & Dead-Zone KPI**: Filter by **Target SSID**, **Specific AP / BSSID** (resolved to friendly `Component.name` via normalized MAC lookup), and **Band** (`All / 2.4 / 5 / 6 GHz`). The **Dead-Zone Threshold Slider** (`-90` to `-50 dBm`) updates the coverage percentage KPI live.
- **Exact Clipped IDW Heatmap**: Computed off the UI thread on a `QThread`, clipped to `floor.outer_polygon_m`, and aligned pixel-for-pixel (row `0` = `max_y` North) with `desktop/coords.py`. Right-click any sample pin to inspect raw BSSID readings or delete outlier samples (undoable).
- **PNG / SVG / PDF Export (`File → Export`)**:
  - **High-DPI PNG** (`300 DPI` equivalent composite)
  - **Vector SVG** (logical topology & floor plans)
  - **Multi-Page ReportLab PDF Report** (Project Overview & KPIs, Port-to-Port Patch Schedule, Physical Cable Length & BOM Schedule, Logical Diagram, and Per-Floor Plans + Clipped Heatmaps)

---

## 9. Zero-Docker Native Android APK Build (`build_apk.ps1` & CI)

NetViz Survey (`android_app/`) is a **100% Native Kotlin + Jetpack Compose + Material 3** Android application (`minSdk = 29`, `compileSdk = 35`, `targetSdk = 35`, Kotlin `2.1.10`, AGP `8.9.1`, Gradle `8.11.1`)—requiring **no Docker, no WSL2, no Buildozer, no Kivy, and no Android Studio GUI**.

### Building on Windows (`android_app/build_apk.ps1`)

```powershell
powershell -ExecutionPolicy Bypass -File android_app\build_apk.ps1
```

On first run, `build_apk.ps1` automatically detects an existing JDK 17–21 and Android SDK, or downloads portable **Eclipse Temurin JDK 17**, **Gradle 8.11.1**, and **Android SDK Command-Line Tools (API 35)** into the gitignored `.toolchains/` folder, runs Android JUnit tests (`GeoMathGoldenTest`, `HeatmapAndScannerTest`, `LanSyncProtocolTest`), and outputs `dist/NetVizSurvey-debug.apk`.

Supported parameters:

| Parameter | Description |
| --- | --- |
| `-SkipTests` | Skip `testDebugUnitTest` and build the APK directly. |
| `-Release` | Build `assembleRelease` instead of `assembleDebug` (outputs `dist/NetVizSurvey-release-unsigned.apk` or signed release APK). |
| `-Install` | Install the built APK onto a connected USB phone via `adb install -r`. |
| `-SetupUsbSync` | Configure `adb reverse tcp:8765 tcp:8765` for USB Cable Sync. |

### Cloud Build Alternative (GitHub Actions)

`.github/workflows/build-android-apk.yml` runs `gradle testDebugUnitTest assembleDebug --no-daemon` on `ubuntu-latest` with JDK 17 + Gradle `8.11.1` and uploads `NetVizSurvey-debug.apk` as a downloadable workflow artifact.

---

## 10. LAN Sync, Windows Firewall Troubleshooting & USB ADB Reverse Sync

### Desktop LAN Sync Server (`Tools → LAN / USB Mobile Sync...`)

`desktop/lan_sync.py` pre-stages an atomic `staging_project.netviz` snapshot (with byte size and SHA-256) before serving so HTTP transfers never block on ZIP compression:

- **Smart Windows NIC Filtering**: `list_network_interfaces()` excludes/deprioritizes `Loopback`, `169.254.x.x`, `vEthernet (WSL)`, `vEthernet (Default Switch)`, `DockerNAT`, `Hyper-V`, `VirtualBox`, `VMware`, and VPN tunnels when real Wi-Fi/Ethernet LAN NICs are present. Switch adapters anytime from the **Network Interface** dropdown to regenerate the multi-IP QR code.
- **HTTP Endpoints (`0.0.0.0:8765..8775`)**:
  - `GET /api/ping` — Instant reachability & metadata check (`bundle_bytes`, `bundle_sha256`, `token_valid`)
  - `GET /api/project.netviz` — Token-protected (`X-NetViz-Token` / `?token=`) `64 KB` chunked streaming download with `X-NetViz-SHA256`
  - `POST /api/upload-survey` — Token-protected streaming upload (`≤ 50 MB`) with SHA-256 verification that immediately imports into Desktop Tab 4 (`survey_received`)
- **UDP Discovery (`0.0.0.0:8766`)**: Responds to `NETVIZ_DISCOVER_V1` broadcasts so Android can auto-discover the PC in one tap.

### Windows Firewall Troubleshooting

If Android reports a connection timeout:

1. Ensure phone and PC are on the **same Wi-Fi subnet** (watch out for Guest Wi-Fi or corporate AP Client Isolation).
2. In the Desktop **LAN / USB Sync** dialog, check the **Windows Defender Firewall Diagnostics** status pill:
   - Click **`Allow Port in Windows Firewall (Admin)`** to run the elevated `netsh` rule, **or**
   - Copy and run the command from an elevated Command Prompt / PowerShell:

```cmd
netsh advfirewall firewall add rule name="NetViz Studio LAN Sync" dir=in action=allow protocol=TCP localport=8765-8775 profile=private,domain,public & netsh advfirewall firewall add rule name="NetViz Studio LAN Sync UDP" dir=in action=allow protocol=UDP localport=8766 profile=private,domain,public
```

### USB Cable Sync (Zero-Firewall Fallback via ADB Reverse)

If corporate Wi-Fi isolates devices or blocks local ports:

1. Enable **USB Debugging** on your Android device and connect via USB cable.
2. In Desktop **LAN / USB Sync → USB Cable Sync** tab, click **`Setup USB Sync (adb reverse)`** (or run `android_app\build_apk.ps1 -SetupUsbSync`, or manually `adb reverse tcp:8765 tcp:8765`).
3. On Android, open **Import / Export** and tap **`USB (127.0.0.1)`** to sync over `http://127.0.0.1:8765` directly over USB.

---

## 11. Android GPS Calibration, Diagnostics, Offline Map Backdrop & Wi-Fi Scan Throttling

### Foreground-Only Lifecycle & Crash Logger

- **Zero Background Services**: `AndroidManifest.xml` uses strictly foreground permissions (`INTERNET`, `ACCESS_NETWORK_STATE`, `ACCESS_WIFI_STATE`, `CHANGE_WIFI_STATE`, `ACCESS_FINE_LOCATION`, `ACCESS_COARSE_LOCATION`, `NEARBY_WIFI_DEVICES`, `CAMERA` — no `ACCESS_BACKGROUND_LOCATION`, no `FOREGROUND_SERVICE`). `ON_PAUSE`/`ON_STOP` unconditionally halts Wi-Fi scanning, GPS updates, and compass sensors.
- **Crash Safety**: Uncaught exceptions persist to `filesDir/logs/last_crash.txt` and surface a copyable **Previous Crash Report** dialog on next launch.

### Pinpoint GPS, Compass & 1-Tap Indoor Calibration

- Uses Google Play Services `FusedLocationProviderClient` (`PRIORITY_HIGH_ACCURACY`) with automatic runtime fallback to Android `LocationManager` on de-Googled devices, rejecting fixes older than `10 s` and enforcing a configurable walk accuracy gate (default `≤ 12 m`).
- **1-Tap Indoor GPS Calibration**: Tap **`Calibrate`** in the bottom chip bar (or **`Calibrate GPS Here`** inside **GPS Diagnostics**) and tap your true physical spot on the floor plan. Android stores the floor `calibrationOffsetM`, renders a live calibrated chip (`Calibrated (X.Xm)`), and records `source = "gps_calibrated"` + `calibration_offset_m` on captured samples.
- **GPS Diagnostics Sheet**: Tap the top **Location HUD** to inspect raw `lat, lon`, provider, fix age, accuracy, active `GeoAnchor`, local `(X, Y)` meters, calibration offset, and bearing/distance to the building.

### Sub-Pixel Offline `tiles.sqlite` Map Backdrop

- Toggle **`[Map BG]`** on the Android survey canvas to render cached `tiles.sqlite` satellite/OSM tiles behind the floor plan.
- Each `256×256` tile is projected through its 4 exact inverse Web Mercator WGS84 corners → `GeoMath.latLonToLocalM` → screen coordinates using Android `Matrix.setPolyToPoly`, eliminating latitude distortion at any zoom or building rotation.

### Disabling Android "Wi-Fi Scan Throttling"

Android 10+ throttles foreground apps to ~4 Wi-Fi scans per 2 minutes by default. If hardware scan timestamps stay stagnant for `> 15 s` while actively scanning, the app shows an amber banner: *"Android Wi-Fi scan throttling detected (>15s). Disable in Developer Options."*

To enable rapid field walks (`2s / 3s / 5s` intervals):

1. Open Android **Settings → About phone** and tap **Build number** 7 times to unlock Developer Options.
2. Open **Settings → System → Developer options** (or tap the banner's **Developer Options** shortcut).
3. Disable **Wi-Fi scan throttling** (`OFF`).

---

## 12. Global Shortcuts, Validation & Automated Verification

### Key Desktop Shortcuts

| Shortcut | Scope | Action |
| --- | --- | --- |
| `Ctrl+K` | Global | Open Fuzzy Command Palette (jump to devices, IPs, MACs, SSIDs, VLANs, buildings, floors, rooms, actions) |
| `Ctrl+,` | Global | Open Settings & Google Maps API Key |
| `Ctrl+1` .. `Ctrl+4` | Global | Switch between Tabs 1–4 |
| `Ctrl+Z` / `Ctrl+Y` | Global | Undo / Redo (`QUndoStack`) |
| `Ctrl+C` / `Ctrl+V` | Tab 1 | Smart Duplicate selected components |
| `M` | Tab 1 | Toggle floating corner Mini-Map |
| `F` / `Shift+F` | Tabs 1–4 | Fit all / fit selected in view |
| `Middle-Drag` / `Space+Drag` / `H` | Tabs 1–4 | Pan viewport |
| `Shift+O` | Tab 2 Floor Editor | Toggle Onion-Skinning (`level_index - 1`) |

### Verification Commands (PowerShell)

```powershell
# Python Linter (0 errors required)
& "p:\NetViz Studio\.venv\Scripts\ruff.exe" check shared desktop scripts tests

# Full Python Test Suite (including Golden Vectors, Qt interactions, LAN Sync & Exports)
& "p:\NetViz Studio\.venv\Scripts\pytest.exe" tests/ -v

# Android JUnit Tests + Zero-Docker Debug APK Build
powershell -ExecutionPolicy Bypass -File "p:\NetViz Studio\android_app\build_apk.ps1"
```
