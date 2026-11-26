# specs.md — NetViz Studio v2.1 (Complete Master Engineering Specification)

> **Active Specification Modes**: `@silent` + `@feature`
>
> **CRITICAL DIRECTIVES FOR THE CODING AGENT**:
>
> 1. Read this **ENTIRE** specification—especially **Section 4 (Exact Protocols, Math Contracts, State Machines & Algorithms)** and **Section 6 (Non-Negotiable Invariants & Bug-Prevention Contracts)**—before writing a single line of code.
> 2. Execute **Phases 1 through 11** strictly in order. Do **NOT** skip any phase or proceed while any automated test or linter check fails.
> 3. **Zero Placeholders**: Never leave stub buttons, `pass`, `TODO`, `FIXME`, `NotImplementedError`, or unimplemented Compose/Qt handlers.
> 4. **Zero Docker / Zero Buildozer / Zero Kivy**: The Android Companion App is a **100% Native Kotlin + Jetpack Compose + Material 3** application built natively on Windows via Gradle + portable JDK 17 + Android SDK Command-Line Tools (`android_app/build_apk.ps1`) and optionally GitHub Actions CI. Do **NOT** introduce Docker, WSL2, Buildozer, Kivy, or Python-for-Android anywhere.
> 5. **Cross-Language Mathematical & Schema Parity**: Python (`shared/`) and Kotlin (`android_app/`) MUST pass the exact same **Golden Contract Test Vectors** (`tests/golden/geo_and_schema_vectors.json`) so GPS projection, building rotation, tile alignment, and `.netviz` / `.netviz-survey.json` serialization can never drift.

---

## Assumptions & Defaults (`@silent`)

The following concrete engineering decisions have been applied so the coding agent can execute end-to-end without ambiguity:

1. **Android Companion App Architecture (Docker & Kivy Eliminated)**: Replaced Python/Kivy/Buildozer/Docker with a **Native Kotlin (`2.1.10`) + Jetpack Compose (`BOM 2025.05.00`) + Material 3** Android project (`minSdk = 29`, `compileSdk = 35`, `targetSdk = 35`) built directly on Windows using **Gradle `8.11.1`** + **Android Gradle Plugin `8.9.1`** + **Eclipse Temurin JDK 17**. AGP `8.9.1` is intentionally pinned over AGP `9.x` to preserve standard `org.jetbrains.kotlin.android` compatibility with JDK 17 and avoid AGP 9.0 plugin migration breaks.
2. **Zero-Admin Native Windows APK Bootstrapper**: `android_app/build_apk.ps1` automatically detects an existing JDK 17 and Android SDK, or downloads portable **Eclipse Temurin JDK 17**, **Gradle `8.11.1`**, and **Android SDK Command-Line Tools** into a gitignored `.toolchains/` directory inside the repo—requiring **no Docker, no WSL2, no Android Studio GUI, and no admin privileges**. A `.github/workflows/build-android-apk.yml` workflow is also generated as a cloud build alternative.
3. **Desktop Runtime**: Windows 10/11 (x64), Python `3.11` or `3.12`, **PySide6 `6.8.1`** + **PySide6-WebEngine `6.8.1`**, Shapely `2.0.7`, NumPy `2.2.1`, SciPy `1.15.0`, Pillow `11.1.0`, ReportLab `4.2.5`.
4. **Canonical Coordinate & Rotation Convention**: Building-local space is right-handed metric ENU (`+X = Local East`, `+Y = Local North`, meters). `GeoAnchor.rotation_deg` is defined universally across Python, JavaScript, and Kotlin as **clockwise degrees from Geographic True North**. Floor plan canvas rendering maps `screen_x = x_m * PPM`, `screen_y = -y_m * PPM` identically on Qt and Jetpack Compose.
5. **GPS Accuracy & Indoor Calibration**: Android uses **Google Play Services `FusedLocationProviderClient` (`21.3.0`)** with automatic runtime fallback to Android `LocationManager` (`FUSED_PROVIDER` / `GPS_PROVIDER` + `NETWORK_PROVIDER`) on de-Googled devices, plus a freshness/accuracy gate, live GPS Diagnostics drawer, and one-tap **"Calibrate GPS Here"** indoor offset tool.
6. **LAN Sync Reliability**: Desktop pre-stages an atomic `.netviz` snapshot before serving, binds `0.0.0.0`, filters out virtual adapters (WSL2/Docker/Hyper-V/VPN), provides a 1-click **Windows Firewall Rule** helper, broadcasts **UDP LAN Discovery (port `8766`)**, encodes multi-IP QR codes with a pairing token, and supports **USB ADB Reverse Sync** (`adb reverse tcp:8765 tcp:8765`). Android binds LAN HTTP sockets explicitly to the active **Wi-Fi `Network`** (preventing Android cellular data hijack), uses fast `/api/ping` pre-flight (`3s`) + streaming transfers (`60s` read timeout), and includes an in-app CameraX + ZXing QR scanner.
7. **Schema Versioning**: `schema_version = "2.1.0"` with strict backward-compatible parsing for `"2.0.0"` files (all newly added fields carry sensible defaults).

---

## 1. Project Overview & Objective

Build **NetViz Studio**, an offline-first, production-grade monorepo consisting of a **Windows Desktop Studio (Python / PySide6)** and a **Native Android Field Survey Companion APK (Kotlin / Jetpack Compose)** uniting four live-linked workflows:

1. **Tab 1 — Logical Network Visualizer & Component Template Library (Windows Desktop)**:
   - Built-in presets for all **9 component types**: **Passive Switch**, **Active Switch**, **Access Point**, **Firewall**, **Router**, **LWL (Fiber) Converter**, **PowerLAN Adapter**, **Patch Panel**, and **End Device**.
   - **Creatable & Reusable Component Templates**: Create, edit, duplicate, save from any canvas component, import, and export `.netviz-templates.json` templates with bulk port-block generation.
   - Customizable component attributes (`name`, `ip_address`, `mac_address`, `frequencies_ghz`, `ssids`, arbitrary key-value pairs) and ports (`Base100`, `Base1000`, `Base2500`, `Base10G`, `LWL_SM`, `LWL_MM`, `SFP`, `SFP_PLUS`, `PowerLAN`, `Coax`, `Custom`, speed, PoE flag, VLAN tag).
   - **Translucent Nodes & Unmistakable Port-to-Port Visibility**: Semi-translucent node fills (default `65%` alpha, live toolbar slider `30%–95%`), strict Z-layering, color-coded ports with occupied/free rings, utilization badge (`4/26`), cable endpoint port pills (`[eth0]` ↔ `[SFP+1]`), hover-dimming connection trace + bottom **Connection Trace Bar**, and an interactive **Port Patch Table** in the Inspector.
   - **Bulletproof Dual-Mode Port Connecting**: Both **press-drag-release** (18 px screen-space magnetic snap ring, preview wire excluded from hit-testing) AND **click-port-A → click-port-B**, plus Inspector dropdown connect.
   - **Routing, Bundling & Auto-Layout**: Editable polyline waypoints, orthogonal Manhattan routing, multi-cable bundling (draggable shared trunk, non-crossing fan-outs, expand/collapse parallel offset, bundle count badge), customizable cable color/thickness/pattern/VLAN, Align/Distribute toolbar, and 1-click **Auto-Layout Hierarchy**.

2. **Tab 2 — Geo-Referenced Buildings & Precision Multi-Floor CAD Editor (Windows Desktop)**:
   - Dual-engine map served over local `http://127.0.0.1:<port>/assets/map.html`: **Google Maps JS API** (primary when API key configured & online) with automatic fallback to locally bundled **Leaflet 1.9.4** (OpenStreetMap & Esri World Imagery) backed by `tiles.sqlite` offline cache.
   - **Prominent Google Maps API Key Wiring**: Accessible via `File → Settings (Ctrl+,)`, main toolbar `⚙ Settings`, AND Tab 2 toolbar `🔑 Map API Key` button (masked + Show/Hide, stored strictly in `%APPDATA%/NetVizStudio/config.json`, never exported into `.netviz`, live reload without restart).
   - **Flexible Map Building Placement**: Trace outline on map, click-to-place manual offline buildings onto map, drag center Move Handle (`origin_lat, origin_lon`) and outer Rotate Handle (`GeoAnchor.rotation_deg` CW from North) with live `Floor 0` single-source-of-truth polygon sync, offline `lat, lon` + online address Location Jump Bar, and cancellable offline `tiles.sqlite` area prefetcher.
   - **Precision Multi-Floor CAD Vector Editor**:
     - Strict non-panning tool state machine (panning ONLY via Middle-Mouse, `Space+Drag`, or Hand tool `H`).
     - **View-Only Floor Rotation** (`Floor.editor_view_rotation_deg` + **Align View to Edge `A`** + accurate **True North Compass** accounting for BOTH `GeoAnchor.rotation_deg` AND `editor_view_rotation_deg`).
     - **Editable Rooms** (corner vertex handles + midpoint `+` handles), **Safe Deduplicated Double-Click Finishing**, **Typed Exact Metric Wall Length Entry** (`4.5` + `Enter`), **Magnetic Vertex/Wall Snapping + Automatic Shapely Wall Cutting**, **Strictly Wall-Locked Freely Movable Doors**, **Stairs**, **Vertical Holes/Risers**, **Onion-Skinning (`Shift+O`)**, **Live Scale Bar**, and optional **Calibrated Floor Blueprint Image Underlay**.

3. **Tab 3 — Live-Linked Physical Network Mapping & Cable BOM (Windows Desktop)**:
   - Live-linked placement of logical devices onto floor plans, automatic physical cable generation for placed peers, physical waypoints & bundling, clickable cross-floor routing via shared `VerticalHole.riser_group_id` badges, live **Metric Cable Length & Bill-of-Materials (BOM)** summary (including vertical riser height allowance), optional **Pre-Survey Wall-Attenuated Wi-Fi Coverage Estimate**, and PNG/SVG/PDF export.

4. **Tab 4 — Wi-Fi Survey Analysis & High-Res Clipped Heatmaps (Windows Desktop)**:
   - Imports `.netviz-survey.json` or `.netviz` sessions (or auto-loads surveys pushed via LAN Sync).
   - Filter by Session(s) (multi-session merge!), Target SSID, Specific AP/BSSID, and Band (`2.4 / 5 / 6 GHz`), taking strongest-AP RSSI per sample.
   - High-resolution vectorized IDW heatmap strictly clipped to `floor.outer_polygon_m`, aligned pixel-for-pixel with `desktop/coords.py` & `editor_view_rotation_deg`, plus **Dead-Zone Threshold Slider & Coverage % KPI**, outlier sample deletion, dBm legend, and rich hover tooltips.

5. **Native Android Field Survey Companion APK (`android_app/` — Kotlin + Jetpack Compose)**:
   - Built **100% natively on Windows without Docker or WSL2** via `android_app/build_apk.ps1` (auto-bootstraps Temurin JDK 17 + Gradle 8.11.1 + Android SDK CLI Tools into `.toolchains/`) OR via GitHub Actions CI.
   - **Zero-Timeout Multi-Modal Sync & File I/O**:
     1. In-app **QR Code Scanner** (CameraX + ZXing) reading multi-IP LAN payloads + pairing token.
     2. **Auto-Discover Desktop PC** via UDP broadcast (`8766`).
     3. **Wi-Fi-Bound Streaming HTTP** (never hijacked by 4G/5G cellular; `3s` `/api/ping` pre-flight + `60s` chunked streaming with MB progress + SHA-256 verification).
     4. **USB ADB Reverse Sync** (`http://127.0.0.1:8765`).
     5. **Android SAF** (`OpenDocument` / `CreateDocument`) + **Android Share Sheet** (`FileProvider`) + **Built-In Demo Campus Plan**.
   - **Pinpoint GPS & Sub-Pixel Offline Map Backdrop**:
     - `FusedLocationProviderClient` + `LocationManager` fallback, freshness/accuracy gate, live accuracy halo & True-North-aligned compass cone, live **Room Name** / off-building bearing arrow, **1-Tap Indoor GPS Calibration ("I Am Standing Here")**, expandable **GPS Diagnostics Sheet**, automatic building-switch suggestion, and exact 4-corner `Matrix.setPolyToPoly` rendering of offline `tiles.sqlite` tiles at any latitude and rotation.
   - **Strict Foreground-Only Active Scanning**:
     - Zero background permissions or services; `ON_PAUSE`/`ON_STOP` unconditionally halts Wi-Fi, GPS, and compass; `WifiManager.startScan()` + `getScanResults()` polling (plus API 33+ `RECEIVER_NOT_EXPORTED` wakeup) with > 15s Android scan-throttling banner & 1-tap Developer Options shortcut; Manual Tap (with 3× Burst Averaging & Undo), Continuous GPS Walk (with accuracy gate), per-sample atomic draft auto-save, on-device clipped IDW heatmap preview, and built-in **Demo Walk Simulator**.

### Definition of Done

- Windows Desktop launches cleanly via `python -m desktop.main` (`--project`, `--offline`, `--debug-log`).
- All 4 desktop tabs stay live-synced with full `QUndoStack`, `Ctrl+K` Command Palette, 9-rule Validation Dock, Mini-Map, Auto-Save & Crash Recovery, and 1-click Sample Campus Project.
- Drag-and-drop AND click-to-click port connections commit 100% reliably (`pytest-qt` verified).
- Floor editor left-click never pans accidentally; view rotation never alters `GeoAnchor.rotation_deg`; True North compass accounts for both rotations; rooms reshape, finish cleanly on double-click, and auto-cut flush; doors stay strictly wall-locked (`pytest-qt` verified).
- Google Maps API key saves/reloads live; `map.html` serves via local `127.0.0.1` HTTP with offline Leaflet 1.9.4 fallback.
- LAN Sync pre-stages `.netviz`, advertises real LAN NICs (excluding WSL/Docker/VPN virtual NICs), passes `/api/ping`, streams download/upload with SHA-256 verification, responds to UDP discovery, and includes Windows Firewall guidance.
- Android project compiles natively on Windows via `powershell -ExecutionPolicy Bypass -File android_app\build_apk.ps1` into `dist/NetVizSurvey-debug.apk` with **ZERO Docker**, and passes `./gradlew testDebugUnitTest` including the exact **Golden Contract Test Vectors** shared with Python `pytest`.
- `ruff check shared desktop scripts tests` exits `0`, `pytest tests/ -v` passes 100%, and Android JUnit tests pass 100%.

---

## 2. Tech Stack, Exact Pinned Dependencies & Zero-Docker Toolchain

### Windows Desktop (Python `3.11` or `3.12`)

`requirements-desktop.txt`:

```text
PySide6==6.8.1
PySide6-WebEngine==6.8.1
numpy==2.2.1
scipy==1.15.0
shapely==2.0.7
Pillow==11.1.0
requests==2.32.3
reportlab==4.2.5
qrcode==8.0
pytest==8.3.4
pytest-qt==4.4.0
ruff==0.8.6
```

Windows Desktop setup commands:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-desktop.txt
python scripts/fetch_vendor_assets.py
```

### Android Companion App (Native Kotlin + Jetpack Compose — Zero Docker!)

- **Language & JVM**: Kotlin `2.1.10`, JDK `17` LTS (Eclipse Temurin auto-bootstrapped if not present)[[11](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQENTuLAUqfQvhc5ZjYsbnGH9hF9X3UQfLgbN6CgQc4jh__fInCZx6t_eFgMmFCP21fETVwNGzcAJaOfymfcaT8sZbW0CtoULj-z1c8rdY85KbXMQmDDPasUIfQXhQH6gM9j7UkDGeBLzEvuGGQD0_9xzcU1-MKspZBP)][[12](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQHx5ZX6iK3Js8YrhaJ2XfVxJI78wIcrJRQ7XSQ8tI-e15wh95bKqAm6fGIS742ZxwHF9EriT6qCrW4GgR_bl0xOgSaOw6AemTATOPACMjpnkEcXaiyno0bHSbZkgMCrCAnsdSMKcizw)].
- **Build System**: Gradle `8.11.1` + Android Gradle Plugin (AGP) `8.9.1` (pinned for rock-solid JDK 17 & `org.jetbrains.kotlin.android` compatibility)[[2](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQEYds2_skdEf3giFFUxgiNg298COj-X5r-SminR6sgq_8sPb3mUmRlRRFQbmtR3mb0IgXN-l-MA4rTDDgy_Ucf6fQIxq0Vl0G-AMWRnOz9RpweIrbAfiTPzg_Oo)][[5](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQGyjIpw7GRyNU1k7y9wuWUmOR6BTZjLiWIvExV9uHPgkSzXIFBNUA8PkCwbOheTAoD7o9k7RzNf4Tz7HMf78wi6qCIH5P98hwMFM8jPTw6rAjHOEkMCmx3Zb8_hb2YWbbjAXMXXNRSAt_T8C8EAyD0bIavZI8Rxvjyqe4KI_xSnLk7szgESROUzLijhh4yY_xs%3D)].
- **Android SDK Targets**: `compileSdk = 35`, `targetSdk = 35`, `minSdk = 29` (Android 10–15), `buildToolsVersion = "35.0.0"`[[2](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQEYds2_skdEf3giFFUxgiNg298COj-X5r-SminR6sgq_8sPb3mUmRlRRFQbmtR3mb0IgXN-l-MA4rTDDgy_Ucf6fQIxq0Vl0G-AMWRnOz9RpweIrbAfiTPzg_Oo)].
- **UI & Core Libraries**:
  - `androidx.compose:compose-bom:2025.05.00` (`ui`, `ui-graphics`, `ui-tooling-preview`, `material3`, `material-icons-extended`)[[3](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQG10GRqcaHU8ehANXzqYIcTHhOOr9v0U7F53ADHOUIdtejaSJd6leB4PkcZw_jlMU9ewI13tlVGqkmyEn8LVwXvUk7uWuoOYftLTqTW6aH_f6lsnHxif9NEj_GmALhyJxZ_znLgMbcVnAAQ1YJvILxP9fXN4rRxYgBU4UwXSpeLlTVBytCq45QjVjhMgtXOF80ztF6QO0wDftTgxQ0FcLKxB-ZG6LkTPzA3W-2YidIV55T5Gw%3D%3D)][[4](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQEDgcpnmPRuSDx140kx41fthqA2cZCChrj3c5EJAxMl3E4p4Xah5LupQY1wC3N8TqMwvd2aRWM-eNSeo2LKJlKHrZLmScdzlpdbqQ6mgi8288D1RV-z766745G96w323xSxCbIDY19a1AkMFSdvcT3gFh1RZPE%3D)]
  - `androidx.core:core-ktx:1.16.0`[[3](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQG10GRqcaHU8ehANXzqYIcTHhOOr9v0U7F53ADHOUIdtejaSJd6leB4PkcZw_jlMU9ewI13tlVGqkmyEn8LVwXvUk7uWuoOYftLTqTW6aH_f6lsnHxif9NEj_GmALhyJxZ_znLgMbcVnAAQ1YJvILxP9fXN4rRxYgBU4UwXSpeLlTVBytCq45QjVjhMgtXOF80ztF6QO0wDftTgxQ0FcLKxB-ZG6LkTPzA3W-2YidIV55T5Gw%3D%3D)]
  - `androidx.activity:activity-compose:1.10.1`[[3](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQG10GRqcaHU8ehANXzqYIcTHhOOr9v0U7F53ADHOUIdtejaSJd6leB4PkcZw_jlMU9ewI13tlVGqkmyEn8LVwXvUk7uWuoOYftLTqTW6aH_f6lsnHxif9NEj_GmALhyJxZ_znLgMbcVnAAQ1YJvILxP9fXN4rRxYgBU4UwXSpeLlTVBytCq45QjVjhMgtXOF80ztF6QO0wDftTgxQ0FcLKxB-ZG6LkTPzA3W-2YidIV55T5Gw%3D%3D)]
  - `androidx.lifecycle:lifecycle-runtime-ktx:2.9.0`, `androidx.lifecycle:lifecycle-runtime-compose:2.9.0`, `androidx.lifecycle:lifecycle-viewmodel-compose:2.9.0`[[3](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQG10GRqcaHU8ehANXzqYIcTHhOOr9v0U7F53ADHOUIdtejaSJd6leB4PkcZw_jlMU9ewI13tlVGqkmyEn8LVwXvUk7uWuoOYftLTqTW6aH_f6lsnHxif9NEj_GmALhyJxZ_znLgMbcVnAAQ1YJvILxP9fXN4rRxYgBU4UwXSpeLlTVBytCq45QjVjhMgtXOF80ztF6QO0wDftTgxQ0FcLKxB-ZG6LkTPzA3W-2YidIV55T5Gw%3D%3D)]
  - `org.jetbrains.kotlinx:kotlinx-serialization-json:1.8.1`[[13](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQEmZldgMQ2jiMRiF8eL0ZylEEXKEwcpmnhrypIsh-dT4ISArXe6BbkReAWjKA3UkllJOU_h2stdOQyBRnFdxnetp9cpLrmNaGPjT2-roOns5MmT6sVF5akvaW0mX3nG9d1D0cvKjGscB7LTy9hQFSB8456nDuxvQwiD7Kwn-JHkMKjefEtL6MGpFA%3D%3D)]
  - `org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.1`
  - `com.google.android.gms:play-services-location:21.3.0`[[10](https://www.google.com/url?sa=E&q=https%3A%2F%2Fvertexaisearch.cloud.google.com%2Fgrounding-api-redirect%2FAUZIYQEx_3FF1iRYDPs496Z_ZXaigClldtc1UnfBDAKuYC_-jZ0f-n1fL8Wwic3QiKXrcfOptPCgoR6nMCP7VKn-CkDTlYJiqjw8vTzdPfqqg5ImFtGlTZ7y6AvH8l3qsDF99EV1Rwsndw5SuLakzVb9nVUCya383KIKh--oioA0p2-A2zyFy_-piA%3D%3D)] (graceful runtime fallback to platform `LocationManager` when Play Services is unavailable)
  - `androidx.camera:camera-core:1.4.2`, `camera-camera2:1.4.2`, `camera-lifecycle:1.4.2`, `camera-view:1.4.2` + `com.google.zxing:core:3.5.3` (100% on-device QR scanner with zero Play Services dependency)
  - Unit tests: `junit:junit:4.13.2`, `org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.1`

---

## 3. Target Directory & File Structure

```text
netviz-studio/
├── .gitignore                                         # Ignores .venv/, .toolchains/, dist/, android_app/.gradle/, android_app/app/build/, local.properties
├── pyproject.toml                                     # Ruff, pytest, and Python project config
├── requirements-desktop.txt                           # Pinned Windows Desktop Python dependencies
├── README.md                                          # Complete Desktop, Map API Key, LAN/USB Sync, GPS Calibration & Zero-Docker APK Build guide
├── .github/
│   └── workflows/
│       └── build-android-apk.yml                      # Cloud CI: runs Android JUnit tests + builds & uploads NetVizSurvey-debug.apk
├── scripts/
│   ├── fetch_vendor_assets.py                         # Downloads & SHA-256 verifies Leaflet 1.9.4 into desktop/ui/map_tab/map_assets/vendor/
│   └── generate_golden_and_demo_assets.py             # Generates tests/golden/geo_and_schema_vectors.json & android_app/app/src/main/assets/sample_campus.json
├── shared/                                            # Pure Python stdlib (+ optional NumPy/Pillow). ZERO PySide6/Shapely/Pydantic!
│   ├── __init__.py
│   ├── models.py                                      # Schema v2.1.0 dataclasses, enums, strict JSON serializer (v2.0.0 backward-compatible)
│   ├── presets.py                                     # All 9 built-in component presets + template instantiate/extract helpers
│   ├── sample_project.py                              # Rich realistic "Sample Campus Project" builder (Berlin lat/lon, 2 buildings, 9 types, survey)
│   ├── validator.py                                   # Live 9-rule project & floor validator
│   ├── geo_math.py                                    # Canonical WGS84 <-> Local ENU (CW rotation_deg), North compass, room lookup, bearing, slippy tiles
│   ├── bundle_io.py                                   # .netviz ZIP I/O (project.json, tiles.sqlite, blueprints/*), survey/template JSON I/O, SHA-256 helpers
│   └── heatmap.py                                     # Vectorized NumPy + pure-Python fallback IDW engine, exact pixel-to-ENU mapping, coverage % KPI
├── desktop/                                           # Windows PySide6 Desktop Studio
│   ├── __init__.py
│   ├── main.py                                        # CLI (--project, --offline, --debug-log), rotating logs, crash recovery, MainWindow
│   ├── config.py                                      # %APPDATA%/NetVizStudio/config.json (Google API key, opacity, prefs), templates.json, autosave/
│   ├── state.py                                       # ProjectState(QObject), QUndoStack, 120s autosave, live cross-tab signals, floor0->footprint sync
│   ├── coords.py                                      # Single desktop coordinate transform: ENU meters <-> Qt scene pixels (PPM=40.0, Y inverted)
│   ├── floor_geometry.py                              # Shapely auto-cut room clipping, magnetic snapping, wall-locked door projection & reconciliation
│   ├── tile_server.py                                 # Local 127.0.0.1 HTTP server serving /assets/* (map.html) AND /tiles/<provider>/<z>/<x>/<y>
│   ├── exporters.py                                   # High-DPI PNG, SVG, and multi-page ReportLab PDF (Patch Schedule + Physical Cable BOM + Heatmaps)
│   ├── lan_sync.py                                    # Hardened LAN Sync HTTP server (atomic snapshot, NIC filter, UDP 8766 discovery, Firewall & USB ADB helper, QR dialog)
│   └── ui/
│       ├── __init__.py
│       ├── main_window.py                             # 4-tab QMainWindow, menus, toolbar, Settings, Validator dock, Ctrl+K, Connection Trace Bar
│       ├── theme.py                                   # Dark CAD QSS stylesheet, translucent node palette, PortType & ComponentType colors
│       ├── dialogs/
│       │   ├── __init__.py
│       │   ├── settings_dialog.py                     # Google Maps API Key (Show/Hide, referrer note), Provider, Offline Cache Margin/Zooms, Save & Reload
│       │   ├── template_manager.py                    # Template CRUD, bulk port generator, clone, Import/Export .netviz-templates.json
│       │   ├── command_palette.py                     # Ctrl+K fuzzy search & jump across components, IPs, MACs, SSIDs, VLANs, buildings, floors, rooms, cables
│       │   └── validation_panel.py                    # Dockable live 9-rule issue list with 1-click jump & filter
│       ├── widgets/
│       │   ├── __init__.py
│       │   ├── component_palette.py                   # Searchable "Built-In Types (9)" + "My Custom Templates" + "+ New Template"
│       │   ├── component_inspector.py                 # Attributes, SSIDs, frequencies, custom K/V, interactive Port Patch Table, Bulk Add Ports, Save as Template
│       │   ├── cable_inspector.py                     # Endpoint port switcher, VLAN, color, line pattern, width, physical length readout, bundle controls
│       │   └── minimap_widget.py                      # Floating interactive corner viewport minimap overlay (M toggle)
│       ├── logical_tab/
│       │   ├── __init__.py
│       │   ├── graphics_items.py                      # Translucent ComponentNodeItem, PortAnchorItem, RoutableCableItem (port pills), CableBundleItem, WaypointHandle
│       │   ├── routing_controller.py                  # Orthogonal Manhattan routing, waypoints, non-crossing bundle fan-outs, Align/Distribute, Auto-Layout
│       │   └── logical_view.py                        # Tab 1: Palette | LogicalCanvasView (centralized drag+click connector, Opacity slider, Minimap) | Inspector
│       ├── map_tab/
│       │   ├── __init__.py
│       │   ├── map_bridge.py                          # QWebChannel Python <-> JS bridge with on_map_ready() handshake & JS console forwarding
│       │   ├── map_assets/
│       │   │   ├── map.html                           # Dual-engine map page served via http://127.0.0.1:<port>/assets/map.html
│       │   │   ├── map_controller.js                  # Google Maps + local Leaflet 1.9.4 fallback + canvas fallback, trace, place, move/rotate handles
│       │   │   └── vendor/
│       │   │       ├── leaflet.js                     # Bundled Leaflet 1.9.4 JS (fetched via scripts/fetch_vendor_assets.py)
│       │   │       └── leaflet.css                    # Bundled Leaflet 1.9.4 CSS
│       │   ├── floor_editor.py                        # Precision CAD vector editor (strict tool mode, view-only rotation, True North compass, editable rooms, safe double-click, typed wall lengths, Shapely auto-cut, wall-locked movable doors, blueprint underlay, onion-skinning, scale bar)
│       │   └── map_building_view.py                   # Tab 2: Map Toolbar ("Map API Key" button) + Geo Map + Floor Editor + Building/Floor Tree
│       ├── physical_tab/
│       │   ├── __init__.py
│       │   └── physical_view.py                       # Tab 3: Unplaced/Placed devices, auto-linked cables, bundling, riser jumps, Cable BOM panel, Wi-Fi coverage estimate
│       └── survey_tab/
│           ├── __init__.py
│           └── survey_view.py                         # Tab 4: Multi-session import/merge, SSID/AP/band filters, clipped IDW heatmap, Dead-Zone % KPI, sample delete, dBm legend
├── android_app/                                       # 100% Native Kotlin + Jetpack Compose Android Companion App (ZERO Docker!)
│   ├── build_apk.ps1                                  # Native Windows zero-Docker build script (auto-bootstraps JDK 17 + Gradle 8.11.1 + Android SDK CLI Tools + ADB install)
│   ├── settings.gradle.kts                            # Root Gradle settings
│   ├── build.gradle.kts                               # Root Gradle plugins (AGP 8.9.1, Kotlin 2.1.10, Compose, Serialization)
│   ├── gradle.properties                              # JVM heap, AndroidX, nonTransitiveRClass, configuration cache
│   └── app/
│       ├── build.gradle.kts                           # App module config (compileSdk 35, minSdk 29, targetSdk 35, Compose BOM 2025.05.00, JUnit)
│       ├── proguard-rules.pro                         # Keep rules for kotlinx.serialization models
│       └── src/
│           ├── main/
│           │   ├── AndroidManifest.xml                # Foreground-only permissions (NO background location, NO foreground service), FileProvider, networkSecurityConfig
│           │   ├── assets/
│           │   │   └── sample_campus.json             # Bundled Demo Campus Project JSON (synced via scripts/generate_golden_and_demo_assets.py)
│           │   ├── res/
│           │   │   ├── xml/
│           │   │   │   ├── network_security_config.xml # Explicit LAN cleartext permission for local Desktop sync
│           │   │   │   └── file_paths.xml             # FileProvider paths for Share Sheet survey export
│           │   │   └── values/
│           │   │       ├── strings.xml
│           │   │       └── themes.xml                 # Dark edge-to-edge Material theme
│           │   └── java/org/netviz/survey/
│           │       ├── MainActivity.kt                # Edge-to-edge Compose host, CrashGuard handler, Lifecycle ON_PAUSE/ON_STOP scan halt, SAF launchers
│           │       ├── model/
│           │       │   └── NetVizModels.kt            # Exact kotlinx.serialization v2.1.0 mirror of shared/models.py (ignoreUnknownKeys = true)
│           │       ├── geo/
│           │       │   ├── GeoMath.kt                 # Bit-for-bit mirror of shared/geo_math.py (CW rotation_deg, room lookup, bearing, slippy tiles)
│           │       │   └── HeatmapEngine.kt           # Fast Kotlin coroutine IDW interpolator + Android Path/Canvas polygon clipping -> ImageBitmap
│           │       ├── data/
│           │       │   ├── BundleRepository.kt        # .netviz ZIP extraction (project.json, tiles.sqlite), SQLite tile reader, atomic draft autosave, Demo loader, SAF I/O
│           │       │   ├── LanSyncClient.kt           # Wi-Fi-bound HTTP client (/api/ping, streaming download/upload, SHA-256 verify, multi-IP probe, UDP 8766 discovery)
│           │       │   └── CrashLogger.kt             # Uncaught exception persistence to filesDir/logs/last_crash.txt + viewer helper
│           │       ├── hardware/
│           │       │   ├── WifiScannerEngine.kt       # Strict foreground WifiManager startScan()+getScanResults() + API 33+ RECEIVER_NOT_EXPORTED + throttle detector
│           │       │   ├── LocationCompassEngine.kt   # FusedLocationProviderClient + LocationManager fallback, freshness/accuracy gate, smoothing, calibration, compass azimuth
│           │       │   └── DemoWalkSimulator.kt       # Built-in realistic indoor walk & RSSI simulator for desk/emulator testing
│           │       ├── state/
│           │       │   └── SurveyViewModel.kt         # StateFlow MVVM controller: Manual tap + 3x burst, GPS walk + accuracy gate, Undo, Calibration, auto-building switch, Sync
│           │       └── ui/
│           │           ├── theme/Theme.kt             # Dark CAD Material 3 palette matching Desktop
│           │           ├── SurveyScreen.kt            # Main layout: Top bar, Live Location & Signal HUD, FloorCanvas, Bottom Scan Controls, Throttle Banner
│           │           ├── FloorPlanCanvas.kt         # 60fps Compose Canvas: setPolyToPoly offline tiles.sqlite backdrop, rooms, doors, stairs, risers, APs, GPS dot/halo/cone, pins, heatmap
│           │           ├── SyncDialogs.kt             # Import/Export sheets: QR Scanner, UDP LAN Discovery, Manual/USB ADB URL + Test Connection diagnostics, SAF, Share Sheet
│           │           ├── QrScannerSheet.kt          # CameraX + ZXing pure-JVM QR scanner for instant Desktop pairing
│           │           └── GpsDiagnosticsSheet.kt     # Live GPS coordinates, provider, fix age, accuracy, GeoAnchor, local (X,Y), 1-tap Calibrate Here & Reset
│           └── test/java/org/netviz/survey/
│               ├── GeoMathGoldenTest.kt               # Loads tests/golden/geo_and_schema_vectors.json and asserts < 1e-4 m parity with Python!
│               ├── HeatmapAndScannerTest.kt           # Tests IDW clipping, foreground lock, throttle detection, 3x burst averaging, calibration offset, draft restore
│               └── LanSyncProtocolTest.kt             # Local Mock HttpServer test verifying /api/ping, multi-IP failover, SHA-256 check, streaming upload/download
└── tests/
    ├── __init__.py
    ├── golden/
    │   └── geo_and_schema_vectors.json                # Shared cross-language golden vectors verified by BOTH pytest AND Android JUnit!
    ├── test_models_and_io.py                          # Schema v2.1.0 + v2.0.0 compat, 9 presets, templates, .netviz ZIP round-trip, zero pydantic/kivy check
    ├── test_geo_math.py                               # Golden vector verification, Berlin high-latitude rotated ENU round-trip (< 1e-4 m), True North compass, tiles
    ├── test_floor_geometry.py                         # Shapely auto-cut room clipping, magnetic snapping, wall-locked door projection & reconciliation
    ├── test_logical_canvas_interaction.py             # pytest-qt: drag-and-drop port connect (8px off-center), click-to-click, occupied block, non-moving node, alpha
    ├── test_floor_editor_interaction.py               # pytest-qt: left-click draws (NOT pans), safe double-click, typed wall length, view rot != map rot, True North compass, room reshape, wall-locked door drag
    ├── test_bundling_and_layout.py                    # Waypoints, orthogonal routing, non-crossing bundle fan-out, parallel offsets, align/distribute, auto-layout
    ├── test_validator.py                              # All 9 validation rules
    ├── test_tile_cache_and_lan_sync.py                # SQLite tiles, /assets/map.html 200, API key persistence/exclusion, LAN Sync /api/ping, NIC filtering, SHA-256 download/upload, UDP discovery
    └── test_heatmap_and_bom.py                        # Exact pixel-to-ENU alignment (non-flipped!), NumPy & pure-Python parity, clipping, dead-zone %, physical cable BOM
```

---

## 4. Architecture, Exact Protocols, Math Contracts & State Machines

### 4.1 Universal Data Schema & Cross-Language Contract (`shared/models.py` ↔ `NetVizModels.kt`)

**Strict Dependency Rule**: `shared/` uses Python `3.11+` standard library (`dataclasses`, `enum`, `uuid`, `json`, `zipfile`, `sqlite3`, `hashlib`, `math`) plus optional `numpy`/`PIL` inside `shared/heatmap.py` ONLY. NEVER import `pydantic`, `PySide6`, `shapely`, or `kivy` in `shared/`.

#### JSON Wire Format Rules (Enforced Identically in Python & Kotlin)

- All field names are `snake_case`.
- Enums serialize to their exact lowercase/canonical `.value` string.
- `Point2D` serializes as `{"x": 1.25, "y": 3.50}`.
- `footprint_latlon` elements serialize as 2-element JSON arrays `[lat, lon]`.
- Both Python `from_dict` and Kotlin `Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = true }` MUST ignore unknown future keys and supply defaults for missing optional v2.1 fields (guaranteeing backward compatibility with v2.0 `.netviz` files).

#### Enums

```python
class ComponentType(str, Enum):
    PASSIVE_SWITCH = "passive_switch"
    ACTIVE_SWITCH = "active_switch"
    ACCESS_POINT = "access_point"
    FIREWALL = "firewall"
    ROUTER = "router"
    LWL_CONVERTER = "lwl_converter"
    POWERLAN_ADAPTER = "powerlan_adapter"
    PATCH_PANEL = "patch_panel"
    DEVICE = "device"

class PortType(str, Enum):
    BASE100 = "Base100"
    BASE1000 = "Base1000"
    BASE2500 = "Base2500"
    BASE10G = "Base10G"
    LWL_SM = "LWL_SM"
    LWL_MM = "LWL_MM"
    SFP = "SFP"
    SFP_PLUS = "SFP_PLUS"
    POWERLAN = "PowerLAN"
    COAX = "Coax"
    CUSTOM = "Custom"

class LinePattern(str, Enum):
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    DASH_DOT = "dash_dot"

class PositionSource(str, Enum):
    MANUAL = "manual"
    GPS = "gps"
    GPS_CALIBRATED = "gps_calibrated"
```

#### Complete Dataclasses (`schema_version = "2.1.0"`)

```python
def new_id() -> str:
    return str(uuid.uuid4())

@dataclass
class Point2D:
    x: float
    y: float

@dataclass
class PortSpec:
    label: str
    port_type: PortType = PortType.BASE1000
    custom_type_name: str | None = None
    speed_mbps: int | None = 1000
    poe_capable: bool = False

@dataclass
class Port(PortSpec):
    id: str = field(default_factory=new_id)

@dataclass
class ComponentTemplate:
    template_name: str
    component_type: ComponentType
    id: str = field(default_factory=new_id)
    category: str = "Custom"
    description: str = ""
    default_name_prefix: str = "DEV"
    accent_color_hex: str | None = None
    default_frequencies_ghz: list[float] = field(default_factory=list)
    default_ssids: list[str] = field(default_factory=list)
    default_custom_attributes: dict[str, str] = field(default_factory=dict)
    port_specs: list[PortSpec] = field(default_factory=list)
    is_builtin: bool = False

@dataclass
class Component:
    component_type: ComponentType
    name: str
    id: str = field(default_factory=new_id)
    template_id: str | None = None
    ip_address: str = ""
    mac_address: str = ""
    frequencies_ghz: list[float] = field(default_factory=list)
    ssids: list[str] = field(default_factory=list)
    custom_attributes: dict[str, str] = field(default_factory=dict)
    ports: list[Port] = field(default_factory=list)
    logical_pos: Point2D = field(default_factory=lambda: Point2D(0.0, 0.0))
    physical_building_id: str | None = None
    physical_floor_id: str | None = None
    physical_pos_m: Point2D | None = None

@dataclass
class CableStyle:
    color_hex: str = "#38BDF8"
    pattern: LinePattern = LinePattern.SOLID
    width_px: float = 2.5

@dataclass
class PhysicalCableSegment:
    floor_id: str
    waypoints_m: list[Point2D] = field(default_factory=list)
    start_hole_id: str | None = None
    end_hole_id: str | None = None

@dataclass
class Cable:
    source_component_id: str
    source_port_id: str
    target_component_id: str
    target_port_id: str
    id: str = field(default_factory=new_id)
    label: str = ""
    vlan_id: str = ""
    style: CableStyle = field(default_factory=CableStyle)
    logical_waypoints: list[Point2D] = field(default_factory=list)
    logical_bundle_id: str | None = None
    physical_segments: list[PhysicalCableSegment] = field(default_factory=list)
    physical_bundle_id: str | None = None

@dataclass
class CableBundle:
    entry_point: Point2D
    exit_point: Point2D
    id: str = field(default_factory=new_id)
    name: str = "Cable Bundle"
    scope: str = "logical"                        # "logical" | "physical"
    floor_id: str | None = None
    waypoints: list[Point2D] = field(default_factory=list)
    member_cable_ids: list[str] = field(default_factory=list)
    style: CableStyle = field(default_factory=lambda: CableStyle(color_hex="#F59E0B", width_px=5.5))
    collapsed: bool = True

@dataclass
class GeoAnchor:
    origin_lat: float
    origin_lon: float
    rotation_deg: float = 0.0                     # Clockwise degrees from True North ONLY

@dataclass
class BlueprintUnderlay:
    archive_path: str                             # e.g. "blueprints/<floor_id>.png" inside .netviz
    meters_per_pixel: float = 0.02
    offset_m: Point2D = field(default_factory=lambda: Point2D(0.0, 0.0))
    rotation_deg: float = 0.0
    opacity: float = 0.45
    visible: bool = True

@dataclass
class Room:
    name: str
    polygon_m: list[Point2D]
    id: str = field(default_factory=new_id)
    fill_color_hex: str = "#1E293B"
    wall_attenuation_db: float = 6.0

@dataclass
class Door:
    position_m: Point2D                           # Strictly locked to a room wall or floor outer wall
    angle_deg: float = 0.0                        # Auto-aligned to host wall segment angle
    width_m: float = 0.9
    swing_flip: bool = False
    hinge_flip: bool = False
    id: str = field(default_factory=new_id)

@dataclass
class Stairs:
    polygon_m: list[Point2D]
    label: str = "Stairs"
    direction_deg: float = 0.0
    id: str = field(default_factory=new_id)

@dataclass
class VerticalHole:
    position_m: Point2D
    riser_group_id: str                           # Matching riser_group_id links vertically across floors
    label: str = "Riser"
    radius_m: float = 0.30
    id: str = field(default_factory=new_id)

@dataclass
class Floor:
    name: str
    outer_polygon_m: list[Point2D]                # Independent shape per floor
    level_index: int = 0
    editor_view_rotation_deg: float = 0.0         # View-only CW canvas rotation; NEVER mutates GeoAnchor!
    blueprint: BlueprintUnderlay | None = None
    rooms: list[Room] = field(default_factory=list)
    doors: list[Door] = field(default_factory=list)
    stairs: list[Stairs] = field(default_factory=list)
    holes: list[VerticalHole] = field(default_factory=list)
    id: str = field(default_factory=new_id)

@dataclass
class Building:
    name: str
    geo_anchor: GeoAnchor | None = None           # None until traced or placed on map
    floor_height_m: float = 3.2                   # Used for physical riser cable length BOM
    footprint_latlon: list[tuple[float, float]] = field(default_factory=list)
    floors: list[Floor] = field(default_factory=list)
    id: str = field(default_factory=new_id)

@dataclass
class WifiReading:
    ssid: str
    bssid: str
    rssi_dbm: int
    frequency_mhz: int

@dataclass
class SurveySample:
    timestamp_utc: str
    building_id: str
    floor_id: str
    pos_m: Point2D
    source: PositionSource
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_accuracy_m: float | None = None
    calibration_offset_m: Point2D | None = None
    readings: list[WifiReading] = field(default_factory=list)
    id: str = field(default_factory=new_id)

@dataclass
class SurveySession:
    name: str
    created_utc: str
    device_model: str = ""
    samples: list[SurveySample] = field(default_factory=list)
    id: str = field(default_factory=new_id)

@dataclass
class MapPreferences:
    preferred_provider: str = "google"            # "google" | "leaflet_osm" | "leaflet_satellite"
    offline_cache_margin_m: float = 250.0
    offline_cache_zooms: list[int] = field(default_factory=lambda: [16, 17, 18, 19])

@dataclass
class Project:
    schema_version: str = "2.1.0"
    id: str = field(default_factory=new_id)
    name: str = "Untitled Network"
    custom_templates: list[ComponentTemplate] = field(default_factory=list)
    components: list[Component] = field(default_factory=list)
    cables: list[Cable] = field(default_factory=list)
    bundles: list[CableBundle] = field(default_factory=list)
    buildings: list[Building] = field(default_factory=list)
    surveys: list[SurveySession] = field(default_factory=list)
    map_preferences: MapPreferences = field(default_factory=MapPreferences)
```

Required helper methods on `Project`:

- `get_component(component_id: str) -> Component | None`
- `get_port(component_id: str, port_id: str) -> Port | None`
- `get_target_ssids() -> list[str]` (sorted unique non-empty SSIDs across all components)
- `is_port_occupied(component_id: str, port_id: str, exclude_cable_id: str | None = None) -> bool`
- `get_port_connection(component_id: str, port_id: str) -> tuple[Cable, Component, Port] | None`
- `get_building(building_id: str) -> Building | None`
- `get_floor(building_id: str, floor_id: str) -> Floor | None`
- `find_component_by_mac(bssid: str) -> Component | None` (case-insensitive normalized MAC lookup for friendly AP names in Tab 4 & Android)

#### Built-In 9 Presets (`shared/presets.py`)

`BUILTIN_TEMPLATES: list[ComponentTemplate]`:

1. `PASSIVE_SWITCH` — prefix `PSW`, color `#64748B`, 5× `Base1000` (`P1`–`P5`, 1000 Mbps)
2. `ACTIVE_SWITCH` — prefix `ASW`, color `#38BDF8`, 24× `Base1000` (`G1`–`G24`, PoE=True) + 2× `SFP_PLUS` (`SFP+1`, `SFP+2`, 10000 Mbps)
3. `ACCESS_POINT` — prefix `AP`, color `#22C55E`, 1× `Base1000` (`eth0`, PoE=True), `frequencies_ghz=[2.4, 5.0]`, `ssids=["Corp-WiFi"]`
4. `FIREWALL` — prefix `FW`, color `#EF4444`, 4× `Base1000` (`WAN`, `LAN`, `DMZ`, `MGMT`) + 2× `SFP` (`SFP1`, `SFP2`)
5. `ROUTER` — prefix `RTR`, color `#F59E0B`, 1× `Base2500` (`WAN`, 2500 Mbps) + 4× `Base1000` (`LAN1`–`LAN4`)
6. `LWL_CONVERTER` — prefix `LWL`, color `#FACC15`, 1× `Base1000` (`RJ45`) + 1× `LWL_SM` (`LC-SM`, 10000 Mbps)
7. `POWERLAN_ADAPTER` — prefix `PLC`, color `#10B981`, 2× `Base1000` (`ETH1`, `ETH2`) + 1× `PowerLAN` (`PWR`, 1200 Mbps)
8. `PATCH_PANEL` — prefix `PP`, color `#94A3B8`, 24× `Base1000` (`PP-01`–`PP-24`)
9. `DEVICE` — prefix `DEV`, color `#A855F7`, 1× `Base1000` (`eth0`)

Helpers:

- `instantiate_from_template(template, existing_names, pos=None) -> Component`: sequential name (`ASW-01`, `ASW-02`…), fresh UUIDs for all `Port`s, copies default attributes.
- `template_from_component(component, template_name, category="Custom", strip_instance_ids=True) -> ComponentTemplate`.

---

### 4.2 Canonical Geo-Projection, Rotation & Sub-Pixel Tile Math (`shared/geo_math.py`, `GeoMath.kt`, `map_controller.js`)

**ELIMINATES "GPS LOCATION DIDN'T MATCH" PERMANENTLY.** All three runtimes (Python, Kotlin, JavaScript) MUST implement the **exact** equations below.

#### A. WGS84 Local Tangent-Plane Scale Constants

Given `anchor = GeoAnchor(origin_lat, origin_lon, rotation_deg)`:
$$\phi = \text{radians}(\text{origin\_lat})$$
$$m_{\text{lat}} = 111132.92 - 559.82\cos(2\phi) + 1.175\cos(4\phi)$$
$$m_{\text{lon}} = 111412.84\cos(\phi) - 93.5\cos(3\phi)$$

#### B. Exact Clockwise Rotation Contract (`rotation_deg`)

- **`rotation_deg` is ALWAYS measured CLOCKWISE from Geographic True North.**
  - When `rotation_deg = 0°`: Building Local `+Y` points Geographic North (`+N`), `+X` points Geographic East (`+E`).
  - When `rotation_deg = +90°`: Building Local `+Y` points Geographic East (`+E`), `+X` points Geographic South (`-N`).
- Let $\theta = \text{radians}(\text{anchor.rotation\_deg})$:
  - **`local_m_to_latlon(pt: Point2D, anchor: GeoAnchor) -> tuple[float, float]`**:
    $$e = pt.x \cdot \cos\theta + pt.y \cdot \sin\theta$$
    $$n = -pt.x \cdot \sin\theta + pt.y \cdot \cos\theta$$
    $$\text{lat} = \text{anchor.origin\_lat} + \frac{n}{m_{\text{lat}}}$$
    $$\text{lon} = \text{anchor.origin\_lon} + \frac{e}{m_{\text{lon}}}$$
  - **`latlon_to_local_m(lat: float, lon: float, anchor: GeoAnchor) -> Point2D`**:
    $$e = (\text{lon} - \text{anchor.origin\_lon}) \cdot m_{\text{lon}}$$
    $$n = (\text{lat} - \text{anchor.origin\_lat}) \cdot m_{\text{lat}}$$
    $$x = e \cdot \cos\theta - n \cdot \sin\theta$$
    $$y = e \cdot \sin\theta + n \cdot \cos\theta$$

#### C. Exact True North Compass & Mobile Heading Cone Formula (Identical on Qt & Android Canvas!)

Both Desktop Qt (`desktop/coords.py`) and Android Jetpack Compose (`FloorPlanCanvas.kt`) render floor plans using:

- Pre-rotation metric-to-screen mapping: `sx = x_m * PPM`, `sy = -y_m * PPM` (Local `+Y` points straight UP on screen).
- View rotation: clockwise rotation by `view_rot_deg` (`Floor.editor_view_rotation_deg` on Desktop; `canvas_rot_deg` on Android).

Therefore, for **any** geographic azimuth `azimuth_deg` (clockwise from True North, e.g. Android compass heading `0° = North`, `90° = East`), its clockwise angle from Screen-Up is:
$$\text{screen\_cw\_deg} = (\text{view\_rot\_deg} - \text{anchor.rotation\_deg} + \text{azimuth\_deg}) \bmod 360$$

- **True North Compass Arrow (`azimuth_deg = 0°`)**: `true_north_screen_cw_deg = (view_rot_deg - anchor.rotation_deg) % 360`.
  - *Sanity Check*: If a building is rotated `30°` CW on the map (`anchor.rotation_deg = 30°`) and `view_rot_deg = 0°`, Building `+Y` faces `N30°E`, so True North points `30°` CCW (`330°` CW) from Screen-Up. If the user rotates the view `+30°` CW, True North points straight up (`0°`).

#### D. Single Source of Truth for `Building.footprint_latlon`

- When tracing a new building on the map, compute the polygon centroid `(origin_lat, origin_lon)` ONCE, create `GeoAnchor(origin_lat, origin_lon, 0.0)`, and convert the traced vertices into `floor0.outer_polygon_m`.
- Thereafter, `floor0.outer_polygon_m` + `building.geo_anchor` is the **SINGLE SOURCE OF TRUTH**. Whenever either `floor0.outer_polygon_m` OR `building.geo_anchor` changes (via map drag/rotate OR floor editor outline edit), deterministically recompute `building.footprint_latlon = floor_polygon_to_latlon(floor0.outer_polygon_m, building.geo_anchor)`! Never re-center `outer_polygon_m` silently.

#### E. Sub-Pixel Exact Offline `tiles.sqlite` Map Backdrop Projection (Android)

- NEVER approximate Web Mercator tile placement linearly without `cos(lat)` correction (at 51°N in Germany, omitting `cos(lat)` produces a ~59% scale error!).
- Instead, for each cached tile `(z, tx, ty)` intersecting the visible viewport:
  1. Compute its 4 exact WGS84 corners via inverse Web Mercator (`tile_xy_to_latlon_bounds(z, tx, ty)` → `(lat_n, lon_w, lat_s, lon_e)`).
  2. Project all 4 corners through `GeoMath.latLonToLocalM(lat, lon, anchor)` → floor local meters → screen pixels `(pNW, pNE, pSE, pSW)`.
  3. Draw the `256×256` tile `Bitmap` using Android `android.graphics.Matrix.setPolyToPoly(...)` mapping `[0,0, 256,0, 256,256, 0,256]` directly onto `[pNW, pNE, pSE, pSW]`!
- Result: **Sub-pixel alignment** between satellite/OSM tiles, floor plan walls, and the live GPS dot at **every latitude, zoom, building rotation, and view rotation**.

#### F. Additional Shared Geo Helpers (`shared/geo_math.py` & `GeoMath.kt`)

- `compute_centroid(latlons)`
- `polygon_latlon_to_local_m(latlons, anchor)` / `floor_polygon_to_latlon(pts_m, anchor)`
- `polygon_area_m2(pts_m)` (Shoelace)
- `polygon_bounds(pts_m) -> tuple[float, float, float, float]` (`min_x, min_y, max_x, max_y`)
- `point_in_polygon(pt_m, polygon_m) -> bool` (ray-casting + inclusive boundary tolerance `1e-6`)
- `find_room_at_point(pt_m, floor) -> Room | None`
- `distance_and_cardinal_bearing(from_pt, to_pt, anchor_rotation_deg=0.0) -> tuple[float, str]`: computes Euclidean distance in meters AND geographic cardinal direction (`"N"`, `"NE"`, `"E"`, `"SE"`, `"S"`, `"SW"`, `"W"`, `"NW"`) accounting for `anchor_rotation_deg`!
- `latlon_to_tile_xy(lat, lon, zoom)` / `tile_xy_to_latlon_bounds(z, x, y)` / `bounding_box_tiles(min_lat, min_lon, max_lat, max_lon, zooms)`

#### G. Cross-Language Golden Contract Test Vectors (`tests/golden/geo_and_schema_vectors.json`)

`scripts/generate_golden_and_demo_assets.py` writes deterministic golden vectors covering:

1. High-latitude Berlin site (`52.520000, 13.405000`) with `rotation_deg = 0.0`, `37.5`, `90.0`, `-125.25`.
2. Equator & Southern Hemisphere sites (`-33.8688, 151.2093`, `rotation_deg = 212.0`).
3. Slippy tile corner conversions at `z = 16, 18, 19`.
4. True North & compass heading screen angles across combinations of `(anchor_rot, view_rot, azimuth)`.
5. Room hit-testing, off-building distance & geographic cardinal bearing.
6. A full serialized `Project` v2.1.0 JSON + a legacy v2.0.0 JSON + a `SurveySession` JSON.
BOTH `pytest tests/test_geo_math.py` AND Android `GeoMathGoldenTest.kt` MUST load and pass every vector within `1e-4 m` / `1e-7 deg`!

---

### 4.3 Bulletproof LAN Sync, UDP Discovery, QR Pairing, Firewall & USB ADB (`desktop/lan_sync.py` ↔ `LanSyncClient.kt`)

**ELIMINATES "IMPORT/EXPORT VIA NETWORK GOT TIMEOUT" PERMANENTLY.**

#### A. Desktop LAN Sync Server (`desktop/lan_sync.py`)

1. **Pre-Staged Atomic Bundle Snapshot**:
   - Before accepting connections, serialize the current project (including `tiles.sqlite` & `blueprints/`) to a temporary file (`staging_project.netviz`) and compute its `size_bytes` and `sha256`.
   - HTTP `GET /api/project.netviz` streams chunks (`64 KB`) immediately with exact `Content-Length` and `X-NetViz-SHA256` headers—NEVER blocking the request thread to build a ZIP on the fly!
   - Include a **"Refresh Snapshot"** button if the user edits the Desktop project while the dialog stays open.
2. **Smart Windows Network Interface Enumeration (No WSL2 / Docker / VPN Trap!)**:
   - Enumerates all active IPv4 addresses on the Windows host (via PowerShell/WMI or `ipconfig` / `socket` route probe), classifying and **excluding/deprioritizing** virtual & non-LAN interfaces (`vEthernet (WSL)`, `vEthernet (Default Switch)`, `DockerNAT`, `Hyper-V`, `VirtualBox`, `VMware`, `Loopback`, `169.254.x.x`, Tailscale/VPN tunnels when a standard LAN NIC exists).
   - Binds `ThreadingHTTPServer(("0.0.0.0", port), ...)` (`port` defaults to `8765`, auto-probing `8765..8775` if in use).
   - Displays a **Network Interface Dropdown** pre-selected to the real Wi-Fi/Ethernet LAN IP and dynamically re-renders the QR code when changed.
3. **REST API Contract (Protected by Short 6-Char Pairing Token `X-NetViz-Token` / `?token=`)**:
   - `GET /api/ping` → `200 OK` JSON:

     ```json
     {"ok": true, "app": "NetVizStudio", "schema_version": "2.1.0", "project_name": "Campus", "bundle_bytes": 1482910, "bundle_sha256": "...", "token_required": true}
     ```

     *(Allows instant `< 3s` reachability verification without token rejection blocking diagnostics; returns `"token_valid": true|false` when token is provided!)*
   - `GET /api/project.netviz` → `200 OK`, `application/octet-stream`, `Content-Length`, `X-NetViz-SHA256`, chunked `64 KB` stream.
   - `POST /api/upload-survey` → streams body (up to `50 MB`), verifies optional `X-NetViz-SHA256`, parses & validates `SurveySession` via `shared.models`, returns `200 OK {"ok": true, "session_id": "...", "sample_count": 42}`, and emits Qt signal `survey_received(SurveySession)` to immediately import into Desktop Tab 4 with a confirmation toast!
4. **Zero-Config UDP LAN Discovery Responder (Port `8766`)**:
   - Background daemon thread listens on UDP `0.0.0.0:8766` (`SO_REUSEADDR`, `SO_BROADCAST`).
   - Upon receiving `"NETVIZ_DISCOVER_V1"`, immediately replies unicast to the sender with JSON:
     `{"app":"NetVizStudio","host":"<hostname>","ips":["192.168.1.42"],"port":8765,"token":"A7K9P2","project_name":"Campus"}`.
5. **Compact Multi-IP QR Payload**:

   ```json
   {"v":2,"ips":["192.168.1.42","192.168.1.43"],"port":8765,"token":"A7K9P2","project":"Campus"}
   ```

6. **Built-In Windows Defender Firewall Diagnostics & 1-Click Fix**:
   - When the LAN Sync dialog opens on Windows, checks (non-blocking) if Windows Firewall allows inbound TCP on the active port and inspects active network profile (`Public` vs `Private`).
   - Displays a clear status pill: 🟢 `Firewall Rule Active` OR 🟡 `Windows Firewall may block phone connections` with:
     - 1-click **"Allow Port in Windows Firewall (Admin)"** button running elevated `netsh advfirewall firewall add rule name="NetViz Studio LAN Sync" dir=in action=allow protocol=TCP localport=8765-8775 profile=private,domain,public` + UDP `8766`.
     - Copyable command fallback if user lacks UAC elevation.
   - Live **Connection Activity Log** inside the dialog showing every incoming ping, download, upload, client IP, and byte count!
7. **USB Cable Sync Fallback (ADB Reverse)**:
   - Dialog includes a **"USB Cable Sync"** tab showing exact 1-click button (runs `adb reverse tcp:<port> tcp:<port>` if `adb.exe` is found in `.toolchains/android-sdk/platform-tools` or `PATH`) and instructs user to tap **"USB Sync (`127.0.0.1`)"** on Android—bypassing corporate Wi-Fi AP Client Isolation and firewalls completely!

#### B. Android LAN Sync Client (`LanSyncClient.kt`)

1. **Explicit Wi-Fi Network Socket Binding**:
   - For private LAN targets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), locates the active `Network` carrying `NetworkCapabilities.TRANSPORT_WIFI` via `ConnectivityManager` and opens connections via `wifiNetwork.openConnection(url)`!
   - Prevents Android from silently routing `192.168.x.x` over 4G/5G cellular when a survey Wi-Fi has no internet!
2. **Cleartext Local HTTP Support**:
   - `res/xml/network_security_config.xml` explicitly enables cleartext for LAN HTTP (`<base-config cleartextTrafficPermitted="true" />`), referenced in `AndroidManifest.xml`.
3. **Parallel Multi-IP Probe + Separate Timeouts**:
   - Given a QR payload with `ips[]`, probes `GET http://<ip>:<port>/api/ping` concurrently with **`connectTimeout = 2500 ms`**, **`readTimeout = 2500 ms`** and locks onto the first responding IP!
   - Streaming download (`GET /api/project.netviz`) and upload (`POST /api/upload-survey`) use **`connectTimeout = 5000 ms`**, **`readTimeout = 60000 ms`**, streaming `64 KB` chunks with live progress (`4.2 MB / 18.6 MB`) and **Cancel** button, plus full **SHA-256 verification** before replacing the active project!
4. **Actionable Error Diagnostics (Never Generic "Timeout")**:
   - Distinguishes and displays exact human-readable guidance for:
     - `SocketTimeoutException` → *"Connection timed out to 192.168.1.42:8765. Check: (1) Same Wi-Fi network? (2) Click 'Allow Port in Windows Firewall' on Desktop. (3) Corporate Wi-Fi may block device-to-device traffic — try USB Sync or Save File (SAF)."*
     - `ConnectException` → *"Connection refused — verify Desktop LAN Sync dialog is open and port matches."*
     - `HTTP 401` → *"Pairing token mismatch — rescan QR code or update token."*
     - `SHA-256 Mismatch` → *"Transfer corrupted in transit — tap Retry."*

---

### 4.4 Tab 1 — Logical Network Visualizer (`desktop/ui/logical_tab/`)

#### A. Translucent Nodes & Exact Z-Order Stack

- `ComponentNodeItem` body fill: `QColor(30, 41, 59, alpha)` where `alpha` defaults to `165` (~65% opacity) and updates live from a toolbar **Node Opacity** slider (`30%–95%`, persisted in `config.json`). Border (`1.5 px`), accent header strip, port utilization badge (`4/26`), and label text (`#F8FAFC`) remain `100%` opaque.
- **Strict Z-Value Stack**:
  - `z = -100`: Background grid
  - `z = 10`: Cables & Cable Bundles (default)
  - `z = 20`: `ComponentNodeItem` translucent body
  - `z = 40`: `PortAnchorItem` pins & port labels (ALWAYS above node bodies and cables!)
  - `z = 50`: Cable endpoint port pills (`[eth0]`, `[SFP+1]`)
  - `z = 60`: Hovered / Selected cables & active connection trace (lifts cleanly above translucent nodes!)
  - `z = 65`: Live drag/click connection preview wire & magnetic target lock ring
  - `z = 70`: `WaypointHandle`s, bundle handles & magnetic alignment snap guides

#### B. BULLETPROOF Port-to-Port Connection Protocol (Zero Dropped Connections!)

- **Centralized Event Ownership**: `LogicalCanvasView` intercepts ALL port-connection mouse events in `mousePressEvent`, `mouseMoveEvent`, and `mouseReleaseEvent` BEFORE calling `super()`. `PortAnchorItem` NEVER manages drag-to-connect state itself and NEVER allows its parent `ComponentNodeItem` to start moving when a port is pressed!
- **Scale-Aware Hit-Testing Helper (`find_port_at_scene_pos`)**:

  ```python
  def find_port_at_scene_pos(
      self,
      scene_pos: QPointF,
      hit_radius_screen_px: float = 18.0,
      exclude_port_key: tuple[str, str] | None = None,
  ) -> PortAnchorItem | None:
      """
      Converts hit_radius_screen_px into scene units using current view zoom:
          radius_scene = hit_radius_screen_px / max(abs(self.transform().m11()), 0.05)
      Iterates ALL registered PortAnchorItem instances and returns the closest port
      whose pin_scene_pos() OR expanded label_scene_rect() lies within radius_scene.
      NEVER relies on scene.itemAt() (which hits preview wires or text overlays).
      """
  ```

- **Preview Graphics Excluded from Hit-Testing**:
  - Preview wire (`QGraphicsPathItem`, `z = 65`) and lock ring (`QGraphicsEllipseItem`, `z = 65`) MUST set `setEnabled(False)` and `setAcceptedMouseButtons(Qt.MouseButton.NoButton)`.
- **Dual Mode State Machine (`IDLE` | `DRAG_CONNECTING` | `PENDING_CLICK_CONNECT`)**:
  - **Left Press**:
    - If `PENDING_CLICK_CONNECT`: resolve `target = self._snap_target_port or self.find_port_at_scene_pos(scene_pos, 18.0, exclude_port_key=src.key)`. If target found & free & `target.key != src.key`: commit `_commit_port_connection(src, target)` and reset to `IDLE`. If occupied: flash red ring + status toast. If empty canvas: cancel to `IDLE`. Always `event.accept(); return`.
    - Else probe `hit_port = self.find_port_at_scene_pos(scene_pos, 14.0)`. If found:
      - If occupied: flash red ring + status toast `"Port <label> is already connected to <peer>. Disconnect in Inspector first."`, `event.accept(); return`.
      - Else enter `DRAG_CONNECTING`, store `_connect_src_port = hit_port`, `_connect_press_screen_pos = event.position()`, `_snap_target_port = None`, show dashed preview wire, and `event.accept(); return` (prevents `ComponentNodeItem` drag!).
  - **Mouse Move while `DRAG_CONNECTING` or `PENDING_CLICK_CONNECT`**:
    - Probe `candidate = self.find_port_at_scene_pos(scene_pos, 18.0, exclude_port_key=src.key)`.
    - If `candidate` is occupied (or exact same port): `_snap_target_port = None`, draw red X ring at `candidate.pin_scene_pos()`, red wire.
    - Else if `candidate` is free: set `_snap_target_port = candidate`, draw glowing green magnetic lock ring at `candidate.pin_scene_pos()`, snap preview wire endpoint directly onto `candidate.pin_scene_pos()`.
    - Else: `_snap_target_port = None`, hide lock ring, wire ends at `scene_pos`.
  - **Left Release while `DRAG_CONNECTING`**:
    - `drag_dist_px = (event.position() - self._connect_press_screen_pos).manhattanLength()`.
    - `target = self._snap_target_port or self.find_port_at_scene_pos(scene_pos, 18.0, exclude_port_key=src.key)`.
    - If `drag_dist_px <= 6.0` and `target is None`: transition cleanly to `PENDING_CLICK_CONNECT` (wire follows cursor; status bar: `"Click target port to connect <Comp>[<Port>] (Esc to cancel)"`).
    - Else if `target is not None` and free: commit `_commit_port_connection(src, target)`, hide preview, reset `IDLE`.
    - Else: clean up preview, reset `IDLE`.
  - **`Esc` Key**: cancels immediately and hides preview/lock ring.
  - **Media Mismatch Feedback**: If `src.port_type` and `target.port_type` mismatch media families (e.g., `LWL_SM` ↔ `Base1000`), STILL commit the cable (users may model transceivers/notes) AND display a non-blocking amber toast `"Warning: Fiber connected to Copper port (flagged in Validation Panel)"`.

#### C. Unmistakable Port Visibility, Inspector Patch Table & Templates

- **Port Rack Layout**: ≤ 12 ports → single bottom row (`≥ 28 px` pitch); > 12 ports → clean 2-row rack strip with dynamic node width so labels (`G1`–`G24`, `SFP+1`) never collide.
- **Port Color Coding**: `Base1000` `#38BDF8`, `Base2500/10G` `#06B6D4`, `Base100` `#60A5FA`, `LWL_SM` `#FACC15`, `LWL_MM` `#FB923C`, `SFP/SFP+` `#A855F7`, `PowerLAN` `#10B981`, `Coax` `#94A3B8`, `Custom` `#EC4899`. Free = hollow `2 px` ring; Connected = solid filled dot + glow halo.
- **Endpoint Port Pills (`z = 50`)**: Dark rounded pills (`[eth0]`, `[SFP+1]`) rendered `18 px` from each port pin along the cable (auto-hide `< 0.55x` zoom; always `100%` opaque when hovered/selected).
- **Hover Trace + Bottom Connection Trace Bar**: Hovering any port, cable, or bundle dims unrelated items to `0.30` opacity, lifts connected cables to `z = 60`, pulses both connected ports, and updates the bottom Trace Bar:
  `Core-SW-01 [SFP+1 · LWL_SM] ━━(VLAN 10 · Trunk: Main Riser)━━▶ Floor2-SW [SFP+1 · LWL_SM]`
- **Interactive Port Patch Table (`ComponentInspector`)**: Columns `Label | Type | Speed / PoE | Connected To | Action`. Clicking `Connected To` jumps to & pulses the remote component; `Action` provides 1-click **Connect...** (quick target picker) or **Disconnect**. Also includes **Bulk Add Ports** and **Save Component as Template...**.
- **Component Template Library**: Global library in `%APPDATA%/NetVizStudio/templates.json` + project copies in `Project.custom_templates`. `ComponentPalette` provides live search, collapsible **Built-In Types (9)** + **My Custom Templates**, drag-to-canvas OR double-click spawn, `+ New Template`, and `TemplateManagerDialog` (CRUD, bulk port blocks, clone, Import/Export `.netviz-templates.json`).

#### D. Cable Routing, Multi-Cable Bundling & Auto-Layout (`routing_controller.py`)

- Double-click cable segment → insert draggable `WaypointHandle` (`z = 70`); right-click handle → delete.
- **Auto-Orthogonal Selected**: clean Manhattan polyline.
- **Multi-Cable Bundling**: Select ≥ 2 cables → **Bundle Selected**. Non-crossing fan-out compares `dist(src, entry) + dist(dst, exit)` vs `dist(src, exit) + dist(dst, entry)`. `collapsed=True` draws thick styled trunk + midpoint pill `"<name> (N)"` + hover tooltip listing all member links; `collapsed=False` renders parallel member wires offset by `(i - (N-1)/2) * 5.0 px` along segment normals. Context menu supports **Unbundle**, **Add to Bundle**, **Clear Waypoints**.
- **Align/Distribute & Auto-Layout Hierarchy**: Align (`Left/Center/Right/Top/Middle/Bottom`), Distribute (`H / V`), and 1-click **Auto-Layout Hierarchy** arranging tiers (Tier 0: Router/Firewall → Tier 1: Core/Active Switches → Tier 2: Passive Switches/Patch Panels/LWL/PowerLAN → Tier 3: Access Points & End Devices) with barycenter crossing minimization.
- **Undo Granularity**: Dragging components updates connected cables/bundles at 60fps and pushes a single `MoveComponentsCommand` on mouse release. Deleting a port cleanly removes any attached cable inside one composite `QUndoCommand`.

---

### 4.5 Tab 2 (Part A) — Dual-Engine Geo Map, Google Maps API Key & Building Placement

#### A. Google Maps API Key Wiring (`desktop/config.py`, `settings_dialog.py`)

- Stored solely in `%APPDATA%/NetVizStudio/config.json` (fallback env var `NETVIZ_GOOGLE_MAPS_API_KEY`). **NEVER** written into `.netviz` archives.
- Accessible from **three obvious entry points**:
  1. `File → Settings...` (`Ctrl+,`)
  2. Top main toolbar `⚙ Settings`
  3. Prominent **`🔑 Map API Key`** button directly on Tab 2's map toolbar (focuses straight onto the API Key input).
- `SettingsDialog`: Masked `QLineEdit` + **Show/Hide**, Preferred Provider (`Google Maps (Primary + Leaflet Fallback)`, `Leaflet Satellite (Free)`, `Leaflet OpenStreetMap (Free)`), Offline Cache Margin (`50–2000 m`, default `250 m`), Zoom checkboxes (`15–20`, defaults `16–19`), inline help note explaining required Google Cloud APIs (*Maps JavaScript API*, optional *Geocoding API*) and HTTP Referrer rule (`127.0.0.1/*` or unrestricted), and **"Save & Reload Map"** emitting `settings_changed` for instant live reload without restart.
- When `preferred_provider == "google"` and key is empty, Tab 2 renders a clean dismissible amber banner above the map with a 1-click `[Set Google Maps API Key]` button.

#### B. Local HTTP Server, Handshake, Offline Leaflet & Tile Cache (`desktop/tile_server.py`)

- `TileCacheServer` (`ThreadingHTTPServer(("127.0.0.1", 0), ...)`) serves:
  1. `GET /assets/<path>` → `desktop/ui/map_tab/map_assets/` (`map.html`, `map_controller.js`, bundled `vendor/leaflet.js`, `vendor/leaflet.css`). Eliminates `file:///` CORS/Referer failures with Google Maps JS API!
  2. `GET /tiles/<provider>/<z>/<x>/<y>` (`provider` ∈ `{"osm", "esri_satellite"}`): SQLite hit → `200 OK` offline; miss + online (and not `--offline`) → fetch upstream (`timeout=5.0`, `User-Agent: NetVizStudio/2.1`), store in `tiles.sqlite`, return bytes; offline/error → return crisp generated `256×256` dark slate grid PNG (`"Offline"` watermark).
- **SQLite Tile Schema (`tiles.sqlite`)**:

  ```sql
  CREATE TABLE IF NOT EXISTS tiles (
      provider TEXT NOT NULL,
      z INTEGER NOT NULL,
      x INTEGER NOT NULL,
      y INTEGER NOT NULL,
      mime TEXT NOT NULL,
      data BLOB NOT NULL,
      fetched_at INTEGER NOT NULL,
      PRIMARY KEY (provider, z, x, y)
  );
  ```

- **Zero-Race `QWebChannel` Handshake & JS Console Forwarding**:
  - Attach `QWebChannel` to `web_view.page()` BEFORE calling `web_view.setUrl(...)`.
  - Route `QWebEnginePage.javaScriptConsoleMessage` to Python's rotating logger (`[MapJS] ...`) AND surface Google Maps auth errors (`gm_authFailure`, `RefererNotAllowedMapError`) directly in Tab 2's status banner!
  - Only after JS invokes `bridge.on_map_ready()` does Python send `configure_map(...)` and `sync_buildings(...)`.
  - If Google Maps fails, times out (`4000 ms` watchdog), or offline, `map_controller.js` automatically switches to bundled local Leaflet `1.9.4` (and includes a lightweight native HTML5 `<canvas>` tile-grid fallback if `vendor/leaflet.js` is absent during airgapped unit tests!).
- **Cancellable Offline Tile Prefetcher**: **"Cache Map Tiles Around Buildings"** downloads all `(z, x, y)` tiles covering every anchored building's bbox + `offline_cache_margin_m` across `offline_cache_zooms` for `osm` & `esri_satellite` on a cancellable `QThread` (`0.05s` polite delay) with `QProgressDialog` and packs `tiles.sqlite` inside `.netviz`.

#### C. Tracing, Placing, Moving & Rotating Buildings on the Map

- **Trace New Building**: Draw polygon on Google Maps or Leaflet → JS emits `bridge.on_polygon_traced(latlons_json)` → Python creates `Building` with centroid `GeoAnchor(..., rotation_deg=0.0)` and `Floor 0` initialized from exact `polygon_latlon_to_local_m()`.
- **Place Existing Manual Building**: Unanchored buildings show an amber `"Unplaced"` badge + **"Place on Map"** button → click anywhere on map to anchor its scaled `Floor 0` polygon at that coordinate.
- **Move & Rotate Placed Building**: Selecting a building polygon shows:
  1. **Center Move Handle**: drags `GeoAnchor.origin_lat, origin_lon` live.
  2. **Outer Rotate Handle**: drags `GeoAnchor.rotation_deg` (**CW from True North**, matching Section 4.2B!) live with degree tooltip (`37.5°`).
  - `map_controller.js` projects `floor0.outer_polygon_m` using the exact same `localMToLatLon` formula as Python/Kotlin so the preview polygon on the map and the saved coordinates match down to sub-millimeter precision!
- **Location Jump Bar**: Offline `lat, lon` parser + online Nominatim/Google geocoder (`timeout=5.0`).

---

### 4.6 Tab 2 (Part B) — Precision Multi-Floor CAD Vector Editor (`floor_editor.py` & `floor_geometry.py`)

#### A. Strict Non-Panning Tool State Machine (Identical Across Tabs 2 & 3!)

- **NEVER** leave `QGraphicsView.DragMode.ScrollHandDrag` active as the default!
- **Unified Panning Rule**: Viewport pans **ONLY** on: (1) **Middle-Mouse Drag**, (2) **`Space` + Left-Drag**, OR (3) explicit **Hand Tool (`H`)**.
- **Exact Tool Dispatch Table (`FloorEditorView`)**:

| Tool Mode | Shortcut | `DragMode` | Cursor | Left-Click Behavior |
| --- | --- | --- | --- | --- |
| `SELECT` | `V` | `NoDrag` (manual rubber-band only on empty canvas; handles get priority!) | Arrow | Select/drag Room vertex handle, Midpoint `+` handle, Door (wall-locked!), Stairs, Hole, or Room |
| `EDIT_OUTLINE` | `E` | `NoDrag` | Crosshair | Drag Floor `outer_polygon_m` vertices, click `+` midpoint to insert, right-click vertex to delete |
| `DRAW_ROOM` | `R` | `NoDrag` | Crosshair | Append snapped vertex to draft room (`Backspace` undoes vertex; type number + `Enter` for exact length; start-ring/`Enter`/safe double-click finishes) |
| `PLACE_DOOR` | `D` | `NoDrag` | Crosshair | Place wall-locked Door ONLY when `project_point_to_walls()` hits a valid wall; blocked otherwise |
| `DRAW_STAIRS` | `S` | `NoDrag` | Crosshair | Press-drag-release draws staircase rectangle with tread hatch & direction arrow |
| `PLACE_HOLE` | `O` | `NoDrag` | Crosshair | Place vertical riser hole (snapping within `0.4 m` to adjacent-floor ghost holes) |
| `ALIGN_VIEW_EDGE` | `A` | `NoDrag` | Crosshair | Click any floor/room wall segment → sets `Floor.editor_view_rotation_deg` so that wall becomes horizontal (`0°`) |
| `CALIBRATE_BLUEPRINT` | `B` | `NoDrag` | Crosshair | Click two reference points on loaded floor blueprint image → dialog asks real-world meters → computes exact `meters_per_pixel` |
| `PAN` | `H` | `ScrollHandDrag` | OpenHand | Left-drag pans viewport |

- `Esc` cancels any active draft and returns to `SELECT (V)`.

#### B. View-Only Floor Rotation & Accurate True North Compass

- Maintain explicit `self._zoom: float` and `self._view_rotation_deg: float` and ALWAYS rebuild the view transform cleanly via `self.setTransform(QTransform().scale(self._zoom, self._zoom).rotate(self._view_rotation_deg))` (eliminates cumulative floating-point drift on mouse wheel zoom!).
- Toolbar controls: `-90°`, `-15°`, numeric spinbox (`-180.0°` to `180.0°`), `+15°`, `+90°`, `Reset 0°`, and **`Align View to Edge (A)`**.
- **Invariant**: Updating `Floor.editor_view_rotation_deg` NEVER modifies `Building.geo_anchor.rotation_deg` or any stored `Point2D` vertex!
- **True North Compass Overlay (Top-Right)**: Renders crisp `N ↑` compass rotated clockwise from screen-up by the exact Section 4.2C formula:
  `true_north_cw_deg = (floor.editor_view_rotation_deg - (building.geo_anchor.rotation_deg if building.geo_anchor else 0.0)) % 360.0`!
- **Dynamic Metric Scale Bar (Bottom-Left)**: Automatically selects clean round lengths (`1 m`, `2 m`, `5 m`, `10 m`, `20 m`) rendered at exact current screen zoom!

#### C. Editable Rooms, Safe Double-Click Finishing & Typed Metric Wall Lengths

- **Editing Existing Rooms**: Selecting a `RoomItem` in `SELECT` mode exposes draggable **Corner Vertex Handles** (`z = 70`) and **Midpoint `+` Handles**. Dragging a corner handle snaps live and on release runs `clip_and_cut_room_polygon` + `reconcile_doors_after_wall_change` in a single `QUndoCommand`. Clicking `+` inserts a vertex; right-clicking a corner deletes it (when `len > 3`).
- **Safe Double-Click Polygon Finishing Protocol**:
  1. Close via: (a) clicking glowing green start-vertex ring (`12 px` screen radius), (b) `Enter`, (c) right-click → *Finish Room* / floating button, OR (d) **Double-Click**.
  2. In `mousePressEvent` during `DRAW_ROOM`: if screen distance from immediately previous draft vertex is `< 6.0 px`, **ignore it** (eliminates duplicate zero-length click from double-click!).
  3. In `mouseDoubleClickEvent` during `DRAW_ROOM`: `event.accept()`, strip any trailing vertex `< 6.0 px` from predecessor, if `len >= 3` commit `_commit_draft_room()`, set `_ignore_next_release = True` and `_post_finish_guard_until = time.monotonic() + 0.25` so releasing the double-click NEVER selects or drags the newly created room!
- **Typed Metric Wall Lengths (`@feature`)**: While drawing a room (at least 1 vertex placed), typing digits + decimal (e.g., `4.5`) shows a crisp HUD pill `"Length: 4.5 m (Enter to place)"` and pressing `Enter` places the next vertex at **exact** distance `4.5 m` along the current cursor angle (respecting `Shift` angle snap)!

#### D. Magnetic Snapping, Automatic Shapely Wall Cutting & Wall-Locked Movable Doors (`desktop/floor_geometry.py`)

- **`desktop/coords.py`**: `PIXELS_PER_METER = 40.0`, `local_m_to_scene(pt) -> QPointF(pt.x * PPM, -pt.y * PPM)`, exact inverse `scene_to_local_m(qpt) -> Point2D(qpt.x() / PPM, -qpt.y() / PPM)`. Used identically across Tabs 2, 3, and 4!
- **`snap_editor_point(...)`**: Priority: (1) Existing vertex within `0.35 m` (cyan diamond), (2) Wall segment projection within `0.35 m` (green square), (3) View-rotated H/V alignment guide with existing vertex within `0.25 m`, (4) View-rotated `0.25 m` grid. `Shift` locks active edge to `0° / 45° / 90°` relative to `view_rotation_deg`.
- **`clip_and_cut_room_polygon(new_pts, floor, exclude_room_id=None, clip_to_outer=True, cut_existing_rooms=True) -> list[Point2D] | None`**:
  - Uses `shapely` (`make_valid`, `intersection` with `outer_polygon_m`, `difference` with `unary_union` of other rooms, largest `Polygon`, `area >= 0.25 m²`, `simplify(0.02, preserve_topology=True)`). Toolbar toggles **`Auto-Cut Overlapping Walls`** and **`Clip to Floor Outline`** both ON by default.
- **`project_point_to_walls(pt_m, floor, max_dist_m=1.5) -> WallHit | None`**:
  - Projects onto all segments of `floor.outer_polygon_m` AND every `room.polygon_m`.
  - Placement in `PLACE_DOOR` blocked in open space (`None` → red no-entry cue).
  - Dragging an existing `DoorItem` in `SELECT` mode projects live (`max_dist_m=6.0`) so doors slide smoothly along walls and wrap corners—NEVER detaching into empty space! Inspector controls: Width (`0.60–3.00 m`), **Flip Swing (`Space`)**, **Flip Hinge**, Delete.
  - `reconcile_doors_after_wall_change(floor, max_dist_m=0.5)` re-snaps or removes orphaned doors inside the same `QUndoCommand`.
- **Blueprint Image Underlay (`@feature`)**: Load PNG/JPG per floor, pack into `.netviz` under `blueprints/<floor_id>.png`, calibrate exact scale via 2-point `CALIBRATE_BLUEPRINT (B)` tool, adjust opacity/offset/visibility cleanly beneath vector walls.
- **Multi-Floor Independent Shapes & Onion-Skinning (`Shift+O`)**: Copy floor below OR start blank; onion-skin renders `level_index - 1` at `25%` opacity; vertical riser holes snap within `0.4 m` to adjacent-floor holes and auto-share `riser_group_id`.

---

### 4.7 Tab 3 — Live-Linked Physical Mapping, Cable BOM & Pre-Survey Estimate (`desktop/ui/physical_tab/physical_view.py`)

- Uses exact same `desktop/coords.py` & honors `Floor.editor_view_rotation_deg` + non-panning mouse rules + dynamic Scale Bar + True North Compass.
- Left sidebar: Building/Floor switcher + searchable Unplaced / Placed Logical Components list (drag onto floor to place; live-synced with Tab 1!).
- **Automatic Physical Link Rendering**: When both endpoints of a logical `Cable` are placed on the same floor, automatically renders its physical link (preserving `Cable.style`, editable waypoints, physical bundling, port labels, hover Connection Trace Bar).
- **Inter-Floor Vertical Riser Routing**: When endpoints sit on different floors of the same building, auto-routes through the nearest matching `VerticalHole.riser_group_id` pair and renders clickable floor-jump badges (`"↑ 1st Floor via Riser-A"` — double-click jumps straight to that floor!).
- **Live Physical Cable Length & BOM Panel (`@feature`)**:
  - Calculates exact Euclidean meter length of every physical cable across all segments PLUS vertical riser length `abs(level_a - level_b) * building.floor_height_m` + configurable `10%` slack factor!
  - Groups totals by `PortType` (e.g., `Base1000 (Copper): 142.8 m (6 runs)`, `LWL_SM (Fiber): 38.4 m (1 run)`) and displays individual run length in `CableInspector` & PDF Export.
- **Pre-Survey Wall-Attenuated Wi-Fi Coverage Preview Toggle (`@feature`)**: Optional toggle rendering a fast analytical log-distance path-loss estimate (`RSSI = -35 - 20*log10(d) - wall_crossings * room.wall_attenuation_db`) around placed Access Points so planners can test AP placement before walking the building!

---

### 4.8 Tab 4 — Wi-Fi Survey Analysis, Exact Clipped Heatmaps & Exporters (`desktop/ui/survey_tab/`, `shared/heatmap.py`, `desktop/exporters.py`)

#### A. Exact Pixel-to-ENU Alignment Contract (`shared/heatmap.py`)

**ELIMINATES UPSIDE-DOWN / SHIFTED HEATMAPS FOREVER!**

- Given `bounds = (min_x, min_y, max_x, max_y)` of `floor.outer_polygon_m`, grid `W = max(2, ceil((max_x - min_x) / resolution_m))`, `H = max(2, ceil((max_y - min_y) / resolution_m))`:
  - Pixel `(col, row)` (`0 <= col < W`, `0 <= row < H`) corresponds to exact local metric point:
    $$x_m = \min_x + \frac{(\text{col} + 0.5) \cdot (\max_x - \min_x)}{W}$$
    $$y_m = \max_y - \frac{(\text{row} + 0.5) \cdot (\max_y - \min_y)}{H}$$
    *(Row `0` is TOP = `max_y` = Building Local North!)*
  - **Scene Placement (Identical on Qt & Compose)**:
    - Top-left corner `(col=0, row=0)` maps to `(min_x * PPM, -max_y * PPM)`.
    - Size is `((max_x - min_x) * PPM, (max_y - min_y) * PPM)`.
- **Multi-AP Same-SSID Aggregation**: For each `SurveySample`, filter readings by selected `target_ssids`, optional `bssid_filter`, and optional `band_filter` (`"2.4"`, `"5"`, `"6"`), and take `max(r.rssi_dbm)` (strongest AP signal experienced by a roaming device).
- **5-Stop Perceptual dBm Colormap**: `-30` `#22C55E` → `-60` `#84CC16` → `-70` `#EAB308` → `-80` `#F97316` → `-95` `#EF4444`.
- **Strict Polygon Clipping**: Mask pixels outside `floor.outer_polygon_m` to `alpha = 0`; smoothly fade alpha near `max_radius_m` (`25.0 m`).
- **Coverage KPI & Dead-Zone Calculation**: Returns `HeatmapResult(image, bounds, valid_pixel_count, above_threshold_count, coverage_percent)` for any configurable dead-zone threshold (default `-70 dBm`).

#### B. Tab 4 Features (`survey_view.py`)

- Multi-session checkable list (view 1 session OR merge multiple survey sessions cleanly!).
- Filters: Target SSID, Specific AP/BSSID (showing friendly `Component.name` via `find_component_by_mac`), Frequency Band (`All / 2.4 / 5 / 6 GHz`), Dead-Zone Threshold slider (`-90` to `-50 dBm`) displaying live KPI badge (`"Coverage ≥ -70 dBm: 88.4% (24 samples)"`).
- Right-click any measurement pin to inspect raw BSSID table or **Delete Outlier Sample** (with Undo!).
- Vectorized IDW computation runs strictly off UI thread on `QThread`.

#### C. Exporters (`desktop/exporters.py`)

- High-DPI PNG (300 DPI equivalent), crisp SVG, and multi-page ReportLab PDF report containing:
  1. Project Overview & KPI Summary
  2. Full **Port-to-Port Patch Schedule** table (Source Comp/Port ↔ Target Comp/Port, PortType, VLAN, Cable Label)
  3. **Physical Cable Length & BOM Schedule**
  4. Logical Network Diagram
  5. Per-Building / Per-Floor Plans & Clipped Wi-Fi Survey Heatmaps

---

### 4.9 Native Kotlin + Jetpack Compose Android Companion App (`android_app/`)

**ZERO DOCKER. ZERO BUILDOZER. ZERO KIVY. ZERO PYJNIUS CRASHES.**

#### A. App Architecture & Crash Safety (`MainActivity.kt`, `CrashLogger.kt`)

- Single-activity Jetpack Compose app (`enableEdgeToEdge()`, dark CAD Material 3 theme matching Desktop).
- `CrashLogger.install(applicationContext)` registers a `Thread.setDefaultUncaughtExceptionHandler` writing full stack trace + device model + Android SDK version to `filesDir/logs/last_crash.txt`. On next startup, if `last_crash.txt` exists, shows a non-blocking **"Previous Crash Report"** modal dialog with **Copy** and **Dismiss** buttons.
- All JSON serialization uses `kotlinx.serialization.json.Json { ignoreUnknownKeys = true; encodeDefaults = true; explicitNulls = true }`.

#### B. Strict Foreground-Only Scanning & Permissions (`AndroidManifest.xml`, `WifiScannerEngine.kt`)

- **`AndroidManifest.xml` Permissions ONLY**:

  ```xml
  <uses-permission android:name="android.permission.INTERNET" />
  <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
  <uses-permission android:name="android.permission.ACCESS_WIFI_STATE" />
  <uses-permission android:name="android.permission.CHANGE_WIFI_STATE" />
  <uses-permission android:name="android.permission.ACCESS_FINE_LOCATION" />
  <uses-permission android:name="android.permission.ACCESS_COARSE_LOCATION" />
  <uses-permission android:name="android.permission.NEARBY_WIFI_DEVICES" />
  <uses-permission android:name="android.permission.CAMERA" />
  ```

  *(STRICTLY FORBIDDEN: `ACCESS_BACKGROUND_LOCATION`, `FOREGROUND_SERVICE`, any `<service>` tag!)*
- **Lifecycle Hard Stop**: `MainActivity` observes `Lifecycle.Event.ON_PAUSE` and `ON_STOP` and unconditionally calls `viewModel.onAppBackgrounded()` → cancels all coroutine scan loops, unregisters `WifiManager` receiver, stops `FusedLocationProviderClient` / `LocationManager`, and unregisters `SensorManager`. `ON_RESUME` NEVER auto-starts scanning!
- **Android 10–15 Safe Active Wi-Fi Scanning (`WifiScannerEngine.kt`)**:
  - While `isForeground && isScanning`:
    - Triggers `wifiManager.startScan()` every `scanIntervalMs` (configurable `2s / 3s / 5s`, default `2.5s`) AND polls `wifiManager.scanResults` every `1.5s`.
    - Optionally registers `SCAN_RESULTS_AVAILABLE_ACTION` using `ContextCompat.registerReceiver(..., ContextCompat.RECEIVER_NOT_EXPORTED)` as a fast wakeup signal (100% Android 14/15 API 34–35 compliant, wrapped in `try/catch` so polling always guarantees delivery!).
    - Tracks max `ScanResult.timestamp` (µs since boot). If hardware timestamps stay stagnant for `> 15 seconds` while actively scanning, raises `isScanThrottled = true`, showing an amber banner with a 1-tap **"Open Developer Options"** button (`Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)`).
    - Reads modern `ScanResult.wifiSsid` on API 33+ (falling back to `.SSID` on API 29–32) and strips surrounding quotes cleanly!

#### C. Pinpoint GPS, Compass, Calibration & Diagnostics (`LocationCompassEngine.kt`, `GpsDiagnosticsSheet.kt`)

- **Primary Engine**: `FusedLocationProviderClient` with `LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L).setMinUpdateIntervalMillis(500L).setGranularity(Granularity.GRANULARITY_FINE)`.
- **Automatic Fallback**: If Google Play Services is unavailable on the device, seamlessly falls back to Android `LocationManager` (`FUSED_PROVIDER` on API 31+, else `GPS_PROVIDER` + `NETWORK_PROVIDER`).
- **Freshness & Accuracy Gate**:
  - Rejects stale fixes (`ageMs > 10_000L`).
  - Applies accuracy-weighted exponential smoothing (`alpha = (3.0f / accuracyM).coerceIn(0.15f, 0.75f)`) for crisp visual blue-dot stability while recording raw `gps_lat`, `gps_lon`, `gps_accuracy_m` on every `SurveySample`.
  - In Continuous GPS Walk mode, enforces configurable **Max Accuracy Gate** (default `≤ 12.0 m`, toggleable) so multipath 40m spikes don't pollute the floor plan!
- **1-Tap Indoor GPS Calibration ("I Am Standing Here")**:
  - Tapping **Calibrate GPS** lets the user tap their known physical spot `knownPtM` on the floor plan while GPS has a lock (`rawProjectedPtM`).
  - Stores `calibrationOffsetM = Point2D(knownPtM.x - rawProjectedPtM.x, knownPtM.y - rawProjectedPtM.y)` for the active floor, displays a chip `"GPS Calibrated (4.2m offset) [Reset]"`, applies it live (`effectivePosM = rawProjectedPtM + calibrationOffsetM`), and stores `source = PositionSource.GPS_CALIBRATED` + `calibration_offset_m` on recorded samples!
- **Compass Heading Sensor**: Uses `Sensor.TYPE_ROTATION_VECTOR` → `SensorManager.getRotationMatrixFromVector` + `getOrientation` (+ `GeomagneticField` declination correction when GPS fix is available so heading is **Geographic True North**, NOT magnetic North!) → draws translucent heading cone at exact Section 4.2C screen angle `(viewRot - anchorRot + trueNorthAzimuth) % 360`.
- **Smart Building Auto-Switch Suggestion**: If the live GPS position lies outside the active building's `outer_polygon_m` AND lies inside another anchored building in the project, shows a 1-tap snackbar `"You appear to be inside Building B — [Switch]"`.
- **Expandable GPS Diagnostics Sheet (`GpsDiagnosticsSheet.kt`)**: Tapping the GPS HUD pill opens full live telemetry: Raw `lat, lon`, Provider (`Fused` / `GPS`), Fix Age (`0.6s`), Accuracy (`±3.2m`), Active `GeoAnchor` (`origin_lat, origin_lon, rot°`), Local `(X, Y)` m, Calibration Offset, Bearing/Distance to Building Center, and **Calibrate Here** / **Reset Calibration** buttons.

#### D. Interactive 60fps Survey Canvas (`FloorPlanCanvas.kt`) & Mobile UX (`SurveyScreen.kt`)

1. **Top Navigation Bar**: `[Import]` | Building Dropdown | Floor Dropdown | `[Export]` | Overflow (`Demo Walk Simulator`, `GPS Diagnostics`, `Clear Draft`)
2. **Live Location & Signal HUD Card** (always visible above canvas):
   - **Location Line** (tappable → opens `GpsDiagnosticsSheet`):
     - Inside floor: `🟢 GPS ±3.4m · X 8.4m, Y 5.2m · Room: Server Room`
     - Outside floor: `🟡 GPS ±4.1m · ↗ 14.2m NE of Building A  [Center]`
     - Manual mode: `🎯 Manual Tap · Tap floor to capture`
   - **Live Signal Strength Meter**: Strongest matched project SSID + live RSSI dBm progress bar (`Corp-WiFi: -54 dBm ████░`) + recorded count (`Samples: 14`).
3. **Interactive Touch Canvas (`FloorPlanCanvas.kt`)**:
   - **Gestures**: 1-finger pan, 2-finger pinch-zoom, optional 2-finger rotation (with `[Reset View]` button honoring `Floor.editor_view_rotation_deg`), 1-finger tap in Manual Mode (places target crosshair + triggers scan / 3× Burst), long-press existing sample pin to inspect/delete.
   - **Sub-Pixel Offline `tiles.sqlite` Map Backdrop (`[Map BG]` toggle)**: Queries `BundleRepository.readTile(provider, z, x, y)` using native Android `SQLiteDatabase.openDatabase(..., OPEN_READONLY)` with an `LruCache<String, Bitmap>` (64 tiles) and renders each tile via `nativeCanvas.drawBitmap(bmp, polyMatrix, paint)` computed with `Matrix.setPolyToPoly` (Section 4.2E)!
   - Renders floor outer polygon, named rooms, doors, stairs, vertical riser holes, placed AP markers, color-coded measurement pins, live blue GPS dot + metric-scaled accuracy radius halo + True-North compass cone + viewport-edge offscreen indicator arrow + rotating True North Compass + Metric Scale Bar + toggleable on-device IDW heatmap overlay (`HeatmapEngine.kt` running on `Dispatchers.Default`).
4. **Bottom Scan Control Bar**:
   - Mode Segmented Control: `[Manual Tap]` | `[GPS Walk]`
   - Prominent Primary Action Button: **`▶ START SCAN`** (switches to vivid red **`■ STOP SCAN`** with live pulse & counter)
   - Quick Action Chips: `[3× Burst]` | `[Undo]` | `[Heatmap]` | `[Map BG]` | `[Center GPS]` | `[Calibrate]` | Interval (`2s/3s/5s`)
5. **5-Star Import & Export Sheets (`SyncDialogs.kt`, `QrScannerSheet.kt`)**:
   - **Import**: (1) **Scan Desktop QR Code** (CameraX + ZXing), (2) **Auto-Discover Desktop PC on LAN** (UDP `8766`), (3) **Manual URL / USB ADB (`127.0.0.1:8765`) + Test Connection**, (4) **Open `.netviz` File (SAF)**, (5) **Load Demo Campus Plan** (`assets/sample_campus.json`).
   - **Export**: (1) **Push to Desktop LAN Sync** (streaming + SHA-256 + live progress), (2) **Save File (SAF)**, (3) **Android Share Sheet (`FileProvider`)**.
   - **Crash-Safe Atomic Draft Auto-Save**: Every recorded sample writes atomically (`draft.tmp` → `renameTo` `active_survey.json`) and auto-restores on next launch!

---

### 4.10 Zero-Docker Native Windows APK Build Pipeline (`android_app/build_apk.ps1` + GitHub Actions)

**COMPLETELY ELIMINATES DOCKER, WSL2, `vmmemWSL` OOM, AND BUILDOZER!**

#### A. Hardened Zero-Docker PowerShell Builder (`android_app/build_apk.ps1`)

Supports Windows PowerShell `5.1` AND PowerShell `7+` with optional parameters:

- `-SkipTests` (default: runs JUnit tests before building APK!)
- `-Release` (builds `assembleRelease` instead of `assembleDebug`)
- `-Install` (installs APK onto connected USB phone via `adb install -r`!)
- `-SetupUsbSync` (runs `adb reverse tcp:8765 tcp:8765`!)

**Step-by-Step Script Logic**:

1. **Resolve Paths**: Locates repo root, `android_app/`, `.toolchains/` (gitignored local toolchain cache), and `dist/`.
2. **JDK 17 Discovery / Auto-Bootstrap**:
   - Checks `JAVA_HOME` and system `java -version` for major version `17` (or `21`), OR cached `.toolchains/jdk-17/`.
   - If missing: downloads portable **Eclipse Temurin JDK 17 Windows x64 ZIP** from `https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse` into `.toolchains/jdk-17/` using `Invoke-WebRequest` / `Start-BitsTransfer` and extracts cleanly! Sets `env:JAVA_HOME` for the current session.
3. **Gradle `8.11.1` Discovery / Auto-Bootstrap** *(avoids committing binary `gradle-wrapper.jar` in git!)*:
   - Checks `.toolchains/gradle-8.11.1/bin/gradle.bat`.
   - If missing: downloads `https://services.gradle.org/distributions/gradle-8.11.1-bin.zip`, extracts to `.toolchains/gradle-8.11.1/`.
4. **Android SDK Command-Line Tools Auto-Bootstrap**:
   - Checks `env:ANDROID_HOME`, `env:ANDROID_SDK_ROOT`, `%LOCALAPPDATA%\Android\Sdk`, OR `.toolchains/android-sdk/`.
   - If missing: downloads Android Command-Line Tools Windows ZIP (`https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip`, with dynamic fallback regex lookup from `https://developer.android.com/studio` if Google rotates the build number), extracts inner `cmdline-tools/` directory into `.toolchains/android-sdk/cmdline-tools/latest/` (exact directory layout required by `sdkmanager.bat`!).
   - Accepts licenses automatically (`"y`ny`ny`ny`ny`ny`ny`n" | & $sdkmanager --sdk_root="$sdkRoot" --licenses`) and installs ONLY required minimal packages (`platform-tools`,`platforms;android-35`,`build-tools;35.0.0`).
   - Writes `android_app/local.properties` with escaped `sdk.dir=...`.
5. **Run Android Unit Tests & Build APK**:
   - Unless `-SkipTests`: runs `& $gradleBin testDebugUnitTest --no-daemon` (executes `GeoMathGoldenTest`, `HeatmapAndScannerTest`, `LanSyncProtocolTest`!).
   - Runs `& $gradleBin assembleDebug --no-daemon` (capped cleanly via `gradle.properties`: `org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8`).
   - Copies `android_app/app/build/outputs/apk/debug/app-debug.apk` to `dist/NetVizSurvey-debug.apk` and prints exact file size + SHA-256!
   - If `-Install`: runs `adb.exe install -r dist/NetVizSurvey-debug.apk`.
   - If `-SetupUsbSync`: runs `adb.exe reverse tcp:8765 tcp:8765`.

#### B. GitHub Actions Cloud Backup Workflow (`.github/workflows/build-android-apk.yml`)

- Triggers on push/PR/manual `workflow_dispatch`.
- Uses `actions/checkout@v4`, `actions/setup-java@v4` (`temurin`, `17`), `gradle/actions/setup-gradle@v4` (`gradle-version: '8.11.1'`, `gradle-home-cache-cleanup: true`).
- Runs `gradle testDebugUnitTest assembleDebug` inside `android_app/`.
- Uploads `android_app/app/build/outputs/apk/debug/app-debug.apk` as downloadable GitHub Actions artifact `NetVizSurvey-debug.apk`.

---

### 4.11 Quality-of-Life & Extended Features (`@feature`)

1. **One-Click "Load Sample Campus Project"** (`shared/sample_project.py` + `assets/sample_campus.json`): Realistic Berlin campus (`52.520000, 13.405000`) with 2 geo-anchored buildings (Building A: rotated `22.5°` CW, L-shaped Ground Floor + rectangular 1st Floor; Building B: single floor), rooms, wall-locked doors, stairs, shared vertical risers, all 9 component types, 1 custom reusable template, VLAN-tagged + bundled logical & inter-floor riser cables, and a 25-sample multi-SSID Wi-Fi survey.
2. **Global Fuzzy Command Palette (`Ctrl+K`)**: Instant search & jump across components (name, IP, MAC, SSID, custom K/V), templates, buildings, floors, rooms, and cables (including VLANs) across all 4 tabs.
3. **Live 9-Rule Network & Floor Validation Dock (`shared/validator.py`)**: Continuously flags `DUP_IP`, `DUP_MAC`, `PORT_MEDIA_MISMATCH`, `PORT_DOUBLE_OCCUPIED`, `AP_NO_SSID`, `UNPLACED_COMPONENT`, `CROSS_FLOOR_NO_RISER`, `BUILDING_UNANCHORED`, `DOOR_DETACHED` with 1-click jump to offending item.
4. **Desktop Canvas Ergonomics**: Magnetic alignment snap guides, Align/Distribute toolbar, 1-click **Auto-Layout Hierarchy**, `Ctrl+C`/`Ctrl+V` smart duplicate (auto-increments name, clears duplicate MAC), `F` / `Shift+F` Fit View, floating interactive **Mini-Map (`M`)**, **Node Opacity** slider, and guided empty-state cards.
5. **Auto-Save, Crash Recovery & Logging**: Periodic 120s snapshot to `%APPDATA%/NetVizStudio/autosave/recovery.netviz` with startup restore prompt; rotating logs (`%APPDATA%/NetVizStudio/logs/netviz.log`, 5 MB × 3); CLI flags `--project <path>`, `--offline`, `--debug-log`.

---

## 5. Sequential 11-Phase Implementation Plan

Execute **Phases 1 through 11** strictly in order. Do NOT move to the next phase until the current phase's **Verification** passes 100%.

### Phase 1: Repository Scaffolding, Schema v2.1.0 Models, Presets, `.netviz` I/O & Sample Project

- **Goal**: Establish repo metadata, `.gitignore`, pure-stdlib v2.1.0 models (with v2.0.0 backward compatibility), all 9 built-in component presets, template helpers, `.netviz` / survey / template / SQLite-tile I/O, and Sample Campus Project.
- **Files Affected**:
  - `.gitignore`, `pyproject.toml`, `requirements-desktop.txt`, `README.md`
  - `shared/__init__.py`, `shared/models.py`, `shared/presets.py`, `shared/bundle_io.py`, `shared/sample_project.py`
  - `tests/__init__.py`, `tests/test_models_and_io.py`
- **Implementation Details**:
  - Implement all dataclasses, enums, strict serializer/deserializer, and `Project` helper methods from Section 4.1.
  - Implement `save_netviz`, `load_netviz` (supporting `project.json`, `tiles.sqlite`, `blueprints/*`), `export_survey_session`, `import_survey_session`, `export_templates_json`, `import_templates_json`, `read_tile_from_sqlite`, and `compute_sha256_file` in `shared/bundle_io.py`.
  - Implement realistic Berlin campus `build_sample_campus_project()` in `shared/sample_project.py`.
- **Verification**:
  - `pytest tests/test_models_and_io.py -v` passes 100% (verifying zero `pydantic`/`PySide6`/`shapely`/`kivy` imports in `shared/`, v2.1.0 & v2.0.0 deserialization, all 9 presets, templates, `.netviz` ZIP round-trip with blueprints & tiles, and sample project integrity).

### Phase 2: Canonical Geo-Math, Golden Contract Vectors, Shapely Floor Geometry, Validator & Heatmap Engine

- **Goal**: Implement exact CW-rotation WGS84 ↔ ENU projection & True North compass math, generate shared Cross-Language Golden Test Vectors, implement Shapely auto-cut room clipping & wall-locked doors, 9-rule validator, and exact-aligned IDW heatmap + physical cable BOM calculator.
- **Files Affected**:
  - `shared/geo_math.py`, `shared/validator.py`, `shared/heatmap.py`
  - `scripts/generate_golden_and_demo_assets.py`, `tests/golden/geo_and_schema_vectors.json`
  - `desktop/__init__.py`, `desktop/coords.py`, `desktop/floor_geometry.py`
  - `tests/test_geo_math.py`, `tests/test_floor_geometry.py`, `tests/test_validator.py`, `tests/test_heatmap_and_bom.py`
- **Implementation Details**:
  - Implement exact Section 4.2 equations (`local_m_to_latlon`, `latlon_to_local_m`, `true_north_screen_cw_deg`, `bearing_screen_cw_deg`, `distance_and_cardinal_bearing`, `tile_xy_to_latlon_bounds`, `bounding_box_tiles`).
  - Run `python scripts/generate_golden_and_demo_assets.py` to generate `tests/golden/geo_and_schema_vectors.json`.
  - Implement `desktop/coords.py` (`PPM = 40.0`, Y-inversion) and `desktop/floor_geometry.py` (`snap_editor_point`, `clip_and_cut_room_polygon`, `project_point_to_walls`, `reconcile_doors_after_wall_change`).
  - Implement `shared/heatmap.py` honoring Section 4.8A exact pixel-to-ENU mapping (row 0 = `max_y`) with both NumPy and `force_pure_python=True` paths + `coverage_percent` KPI.
  - Implement physical cable length + riser BOM calculator in `shared/geo_math.py` (or `shared/models.py`).
- **Verification**:
  - `pytest tests/test_geo_math.py tests/test_floor_geometry.py tests/test_validator.py tests/test_heatmap_and_bom.py -v` passes 100%, explicitly proving:
    1. Berlin rotated ENU round-trip error `< 1e-4 m` and golden vector match.
    2. True North compass angle formula verified across `(anchor_rot, view_rot, azimuth)` combinations.
    3. Overlapping rooms auto-cut flush (`overlap_area < 1e-6`), exterior rooms clipped, doors rejected in open space and aligned to walls.
    4. Heatmap sample at `(x=10, y=2)` produces peak green pixel at exact `(col, row)` corresponding to `local_m_to_scene(Point2D(10, 2))` (NOT vertically flipped!), and NumPy vs Pure-Python parity holds.

### Phase 3: Desktop Config, Settings Dialog, Reactive State, Local HTTP Tile Server & Hardened LAN Sync

- **Goal**: Implement `%APPDATA%` config/API-key persistence, `SettingsDialog`, `ProjectState` (Undo/Redo + autosave + `floor0 -> footprint_latlon` single source of truth), dark CAD theme, `TileCacheServer`, and zero-timeout `desktop/lan_sync.py`.
- **Files Affected**:
  - `desktop/config.py`, `desktop/state.py`, `desktop/tile_server.py`, `desktop/lan_sync.py`
  - `desktop/ui/__init__.py`, `desktop/ui/theme.py`, `desktop/ui/dialogs/__init__.py`, `desktop/ui/dialogs/settings_dialog.py`
  - `tests/test_tile_cache_and_lan_sync.py`
- **Implementation Details**:
  - Implement `desktop/config.py` (`google_maps_api_key` strictly local, never exported into `.netviz`), `SettingsDialog` (Show/Hide, referrer note, Save & Reload), and `TileCacheServer` (`/assets/*` + `/tiles/*`).
  - Implement `desktop/lan_sync.py` following **Section 4.3A** verbatim: atomic pre-staged `.netviz` snapshot, Windows real-LAN NIC filter (excluding WSL2/Docker/Hyper-V/Loopback), `0.0.0.0` binding, `/api/ping` + streaming `/api/project.netviz` + validated `/api/upload-survey` with SHA-256 + pairing token, UDP `8766` discovery responder, Windows Firewall diagnostic + 1-click allow button, USB ADB reverse tab, live activity log, and multi-IP QR dialog.
  - Ensure `ProjectState` automatically recomputes `Building.footprint_latlon` whenever `floor0.outer_polygon_m` or `Building.geo_anchor` changes.
- **Verification**:
  - `pytest tests/test_tile_cache_and_lan_sync.py -v` passes 100%: SQLite tile hit/offline placeholder, `/assets/map.html` 200, API key persistence & exclusion from `.netviz`, NIC filter excluding virtual adapters, LAN Sync `/api/ping`, SHA-256 streaming download, survey upload + token check, and UDP `8766` discovery response.

### Phase 4: Tab 1 — Logical Network Visualizer (Translucent Nodes, Templates, Bulletproof Port Connect, Bundling & Auto-Layout)

- **Goal**: Build Tab 1 following Section 4.4 completely.
- **Files Affected**:
  - `desktop/ui/dialogs/template_manager.py`
  - `desktop/ui/widgets/__init__.py`, `desktop/ui/widgets/component_palette.py`, `desktop/ui/widgets/component_inspector.py`, `desktop/ui/widgets/cable_inspector.py`, `desktop/ui/widgets/minimap_widget.py`
  - `desktop/ui/logical_tab/__init__.py`, `desktop/ui/logical_tab/graphics_items.py`, `desktop/ui/logical_tab/routing_controller.py`, `desktop/ui/logical_tab/logical_view.py`
  - `tests/test_bundling_and_layout.py`, `tests/test_logical_canvas_interaction.py`
- **Implementation Details**:
  - Implement translucent `ComponentNodeItem` (`alpha=165`, `z=20`, utilization badge), `PortAnchorItem` (`z=40`), port pills (`z=50`), hovered trace (`z=60`), hit-test-excluded preview (`z=65`).
  - Implement centralized `LogicalCanvasView` dual drag-and-drop + click-to-click connector (`find_port_at_scene_pos` scale-aware `18 px` screen radius, green/red snap lock ring, non-moving parent node, media mismatch amber toast).
  - Implement `ComponentPalette`, `TemplateManagerDialog`, `ComponentInspector` (with interactive Port Patch Table + Bulk Add Ports + Save as Template), `CableInspector` (with VLAN + physical length), `MiniMapWidget`, Align/Distribute, Auto-Layout Hierarchy, and Connection Trace Bar.
- **Verification**:
  - `pytest tests/test_bundling_and_layout.py tests/test_logical_canvas_interaction.py -v` passes 100% (proving 8px off-center drag connect commits exact ports without moving Component A, click-to-click connects, occupied port blocks, translucent alpha updates live, non-crossing bundling & Auto-Layout work).

### Phase 5: Tab 2 (Part A) — Dual-Engine Geo Map, Google Maps API Key UI & Building Placement/Rotation

- **Goal**: Implement local Leaflet 1.9.4 vendor fetcher, HTTP-served `map.html`, zero-race `QWebChannel` handshake with JS console forwarding, `🔑 Map API Key` toolbar button & live reload, building trace/place/move/rotate handles using exact Section 4.2 CW math, Jump Bar, and offline tile prefetcher.
- **Files Affected**:
  - `scripts/fetch_vendor_assets.py`
  - `desktop/ui/map_tab/__init__.py`, `desktop/ui/map_tab/map_bridge.py`
  - `desktop/ui/map_tab/map_assets/map.html`, `desktop/ui/map_tab/map_assets/map_controller.js`
  - `desktop/ui/map_tab/map_assets/vendor/leaflet.js`, `desktop/ui/map_tab/map_assets/vendor/leaflet.css`
  - `desktop/ui/map_tab/map_building_view.py`
- **Implementation Details**:
  - Run `python scripts/fetch_vendor_assets.py` to vendor official Leaflet 1.9.4 cleanly (with built-in HTML5 `<canvas>` tile fallback in `map_controller.js` if absent in airgapped tests).
  - Verify `map_controller.js` `localMToLatLon` uses identical WGS84 constants and CW `rotation_deg` formula as `shared/geo_math.py`.
  - Forward `QWebEnginePage.javaScriptConsoleMessage` to Desktop logs and display Google Maps auth/referrer errors clearly in Tab 2's banner.
- **Verification**:
  - Verify `http://127.0.0.1:<port>/assets/map.html` loads cleanly; verify setting Google API key reloads live; verify tracing, click-to-place, dragging, and rotating a building updates `GeoAnchor` & `footprint_latlon` consistently; verify tile prefetcher writes `tiles.sqlite`.

### Phase 6: Tab 2 (Part B) — Precision Multi-Floor CAD Vector Editor

- **Goal**: Build `floor_editor.py` following Section 4.6 verbatim.
- **Files Affected**:
  - `desktop/ui/map_tab/floor_editor.py`
  - `tests/test_floor_editor_interaction.py`
- **Implementation Details**:
  - Implement strict non-panning tool state machine, explicit `_zoom` + `_view_rotation_deg` transform rebuild, accurate True North Compass (`(view_rot - anchor_rot) % 360`), dynamic Scale Bar, editable rooms (corner + midpoint `+` handles), safe deduplicated double-click finishing + `250 ms` guard, typed metric wall lengths, Shapely auto-cut + clipping toggles, strictly wall-locked movable doors, calibrated Blueprint Underlay (`CALIBRATE_BLUEPRINT (B)`), stairs, vertical riser holes, and onion-skinning (`Shift+O`).
- **Verification**:
  - `pytest tests/test_floor_editor_interaction.py -v` passes 100% (`pytest-qt` verifying: `DRAW_ROOM` left-clicks draw and NEVER pan; double-click finishes with zero duplicate vertex & zero post-finish drag; typed metric length places exact meter segment; view rotation `35°` updates `editor_view_rotation_deg` without changing `GeoAnchor.rotation_deg` and rotates True North compass accurately; room corner drag auto-cuts flush; open-space door click creates `0` doors; wall door click creates `1` door that slides strictly along walls when dragged).

### Phase 7: Tab 3 — Physical Network Mapping, Cable BOM & Pre-Survey Coverage Estimate

- **Goal**: Build live-linked Tab 3 (`desktop/ui/physical_tab/physical_view.py`) honoring Section 4.7.
- **Files Affected**:
  - `desktop/ui/physical_tab/__init__.py`, `desktop/ui/physical_tab/physical_view.py`
- **Implementation Details**:
  - Unplaced/Placed device sidebar, automatic physical cables between placed peers, physical waypoints & bundling, clickable inter-floor riser jump badges, live Metric Cable Length & BOM summary panel, and optional pre-survey wall-attenuated Wi-Fi coverage preview toggle.
- **Verification**:
  - Place Firewall on Floor 0 and AP on Floor 1, confirm live sync with Tab 1, verify automatic riser routing + double-click jump, and confirm BOM length updates live when dragging waypoints.

### Phase 8: Tab 4 — Wi-Fi Survey Analysis, Command Palette, Validator Dock, Exporters & Desktop Entrypoint

- **Goal**: Complete Tab 4, `Ctrl+K` Command Palette, Validation Dock, PNG/SVG/PDF exporters, `MainWindow`, and CLI entrypoint.
- **Files Affected**:
  - `desktop/ui/survey_tab/__init__.py`, `desktop/ui/survey_tab/survey_view.py`
  - `desktop/ui/dialogs/command_palette.py`, `desktop/ui/dialogs/validation_panel.py`
  - `desktop/exporters.py`, `desktop/ui/main_window.py`, `desktop/main.py`
- **Implementation Details**:
  - Multi-session merge, SSID/AP-name/band filters, exact-aligned clipped IDW heatmap on `QThread`, Dead-Zone Threshold slider + Coverage % KPI, outlier sample deletion, dBm legend.
  - Wire LAN Sync `survey_received` directly into `ProjectState` + Tab 4 toast notification!
  - Implement ReportLab PDF exporter including Patch Schedule + Physical Cable BOM + Diagrams + Heatmaps.
  - Wire `--project`, `--offline`, `--debug-log`, rotating log file, and startup `autosave/recovery.netviz` prompt.
- **Verification**:
  - Launch Sample Campus Project, test `Ctrl+K`, Validation Dock, Tab 4 multi-session clipped heatmap + Dead-Zone KPI, and export valid non-empty PNG, SVG, and multi-page PDF files.

### Phase 9: Native Kotlin Android Project — Core Models, GeoMath, Heatmap, Bundle/SQLite & Hardened LAN Sync

- **Goal**: Scaffold the clean native Kotlin + Gradle `8.11.1` / AGP `8.9.1` / Compose Android project (ZERO Docker/Kivy!) and implement all non-UI domain, math, storage, and networking layers with full JUnit coverage.
- **Files Affected**:
  - `android_app/settings.gradle.kts`, `android_app/build.gradle.kts`, `android_app/gradle.properties`
  - `android_app/app/build.gradle.kts`, `android_app/app/proguard-rules.pro`
  - `android_app/app/src/main/AndroidManifest.xml`, `android_app/app/src/main/res/xml/network_security_config.xml`, `android_app/app/src/main/res/xml/file_paths.xml`, `android_app/app/src/main/res/values/strings.xml`, `android_app/app/src/main/res/values/themes.xml`
  - `android_app/app/src/main/assets/sample_campus.json` (generated by `scripts/generate_golden_and_demo_assets.py`)
  - `android_app/app/src/main/java/org/netviz/survey/model/NetVizModels.kt`
  - `android_app/app/src/main/java/org/netviz/survey/geo/GeoMath.kt`, `android_app/app/src/main/java/org/netviz/survey/geo/HeatmapEngine.kt`
  - `android_app/app/src/main/java/org/netviz/survey/data/BundleRepository.kt`, `android_app/app/src/main/java/org/netviz/survey/data/LanSyncClient.kt`, `android_app/app/src/main/java/org/netviz/survey/data/CrashLogger.kt`
  - `android_app/app/src/test/java/org/netviz/survey/GeoMathGoldenTest.kt`, `android_app/app/src/test/java/org/netviz/survey/LanSyncProtocolTest.kt`
- **Implementation Details**:
  - Mirror `shared/models.py` v2.1.0 and `shared/geo_math.py` bit-for-bit in Kotlin.
  - Implement `LanSyncClient.kt` following Section 4.3B verbatim (Wi-Fi `Network` socket binding, parallel multi-IP `/api/ping`, `60s` chunked streaming with progress & cancellation, SHA-256 verification, UDP `8766` discovery, actionable error diagnostics).
  - Run `python scripts/generate_golden_and_demo_assets.py` to populate `assets/sample_campus.json`.
- **Verification**:
  - Execute `GeoMathGoldenTest` and `LanSyncProtocolTest` cleanly via Gradle (verified in Phase 10 builder).

### Phase 10: Native Kotlin Android UI, Hardware Engines, Zero-Docker `build_apk.ps1` & GitHub Actions CI

- **Goal**: Build foreground-only Wi-Fi scanner, FusedLocationProvider + GPS calibration + compass engine, Demo Walk Simulator, 60fps Jetpack Compose canvas with `Matrix.setPolyToPoly` offline `tiles.sqlite` backdrop, CameraX+ZXing QR scanner, SAF/Share/LAN dialogs, hardened zero-Docker Windows `build_apk.ps1`, and CI workflow.
- **Files Affected**:
  - `android_app/app/src/main/java/org/netviz/survey/hardware/WifiScannerEngine.kt`
  - `android_app/app/src/main/java/org/netviz/survey/hardware/LocationCompassEngine.kt`
  - `android_app/app/src/main/java/org/netviz/survey/hardware/DemoWalkSimulator.kt`
  - `android_app/app/src/main/java/org/netviz/survey/state/SurveyViewModel.kt`
  - `android_app/app/src/main/java/org/netviz/survey/ui/theme/Theme.kt`
  - `android_app/app/src/main/java/org/netviz/survey/ui/FloorPlanCanvas.kt`
  - `android_app/app/src/main/java/org/netviz/survey/ui/SurveyScreen.kt`
  - `android_app/app/src/main/java/org/netviz/survey/ui/SyncDialogs.kt`
  - `android_app/app/src/main/java/org/netviz/survey/ui/QrScannerSheet.kt`
  - `android_app/app/src/main/java/org/netviz/survey/ui/GpsDiagnosticsSheet.kt`
  - `android_app/app/src/main/java/org/netviz/survey/MainActivity.kt`
  - `android_app/app/src/test/java/org/netviz/survey/HeatmapAndScannerTest.kt`
  - `android_app/build_apk.ps1`, `.github/workflows/build-android-apk.yml`
- **Implementation Details**:
  - Implement every hardware, lifecycle, UI, calibration, and build pipeline detail in **Sections 4.9 & 4.10**.
  - Verify `AndroidManifest.xml` contains NO `ACCESS_BACKGROUND_LOCATION`, NO `FOREGROUND_SERVICE`, NO `<service>`, and DOES reference `@xml/network_security_config` and `FileProvider`.
  - Verify `build_apk.ps1` runs end-to-end on Windows without Docker, WSL2, or Android Studio GUI.
- **Verification**:
  - Run `powershell -ExecutionPolicy Bypass -File android_app\build_apk.ps1`:
    1. Bootstraps/detects JDK 17 + Gradle `8.11.1` + Android SDK 35 cleanly.
    2. Passes `testDebugUnitTest` 100% (`GeoMathGoldenTest`, `HeatmapAndScannerTest`, `LanSyncProtocolTest`).
    3. Compiles `dist/NetVizSurvey-debug.apk` cleanly (non-empty valid signed debug APK!).

### Phase 11: End-to-End Integration, Full Linting & Comprehensive Documentation

- **Goal**: Verify cross-platform `.netviz` ↔ `.netviz-survey.json` parity, static quality, and complete user/developer documentation.
- **Files Affected**:
  - `README.md` and any final integration adjustments.
- **Implementation Details**:
  - Document: Windows Desktop install & CLI flags, Component Templates, Port-to-Port Drag & Click Connecting, Google Maps API Key & Referrer setup (`127.0.0.1/*`), Placing/Tracing/Rotating Buildings, Floor Editor View Rotation & Shapely Auto-Cut Rooms, Blueprint Underlay Calibration, Wall-Locked Doors, Physical Cable BOM, Zero-Docker Android APK building (`build_apk.ps1` + `-Install` + `-SetupUsbSync` + GitHub Actions), LAN Sync & Windows Firewall troubleshooting, USB ADB Sync fallback, Android GPS Calibration & Diagnostics, and disabling Android Developer Option *"Wi-Fi scan throttling"*.
- **Verification**:
  - `ruff check shared desktop scripts tests` → `0` errors.
  - `pytest tests/ -v` → 100% pass across all Python test suites.
  - `powershell -ExecutionPolicy Bypass -File android_app\build_apk.ps1` → Android JUnit 100% pass + valid `dist/NetVizSurvey-debug.apk`.

---

## 6. Coding Standards, Regression Guardrails & Non-Negotiable Invariants

1. **Zero Placeholders or Stubs**: Every menu item, toolbar button, dialog, tool mode, validator rule, exporter, Compose screen, hardware listener, and sync endpoint MUST be completely implemented. Never leave `pass`, `TODO`, `FIXME`, or `NotImplementedError`.
2. **Strict Type Safety**: Full Python `3.11+` type hints across `shared/`, `desktop/`, `scripts/`, `tests/`; strict null-safe Kotlin across `android_app/`.
3. **Zero Docker / Zero Buildozer / Zero Kivy**: Do NOT generate `buildozer.spec`, Dockerfiles, `kivy` imports, or `pydantic` imports anywhere in the repository.
4. **Cross-Language Math & Schema Parity**: Python (`shared/geo_math.py`), JavaScript (`map_controller.js`), and Kotlin (`GeoMath.kt`) MUST use identical WGS84 constants, identical **CW-from-North** `GeoAnchor.rotation_deg` equations, identical True North compass formula `(view_rot - anchor_rot + azimuth) % 360`, and pass `tests/golden/geo_and_schema_vectors.json`.
5. **Single Source of Truth for Building Footprint**: `floor0.outer_polygon_m` + `Building.geo_anchor` deterministically drives `Building.footprint_latlon`; never let map polygon and floor outline drift out of sync.
6. **Sub-Pixel Offline Tile Alignment**: Android MUST render `tiles.sqlite` bitmaps via exact 4-corner WGS84 → ENU → screen `Matrix.setPolyToPoly` projection (never uncompensated Web Mercator linear scaling).
7. **Zero-Timeout LAN Sync**: Desktop MUST pre-stage `.netviz` before serving, bind `0.0.0.0`, exclude virtual NICs (WSL/Docker/Hyper-V/Loopback) from primary IP selection, expose `/api/ping` + SHA-256 streaming + UDP `8766` discovery + Firewall/USB-ADB helpers. Android MUST bind private-IP sockets to `TRANSPORT_WIFI` `Network`, enable LAN cleartext via `network_security_config.xml`, probe multi-IPs with `2.5s` ping, and stream payload transfers with `60s` read timeout.
8. **Centralized Port Connection Handling**: All Tab 1 drag-and-drop AND click-to-click port connections run through `LogicalCanvasView` with scale-aware `find_port_at_scene_pos(..., 18.0)`, hit-test-excluded preview wires (`setEnabled(False)`, `NoButton`), cached `_snap_target_port`, and non-moving parent nodes.
9. **No Default `ScrollHandDrag`**: Panning on Desktop editors occurs ONLY on Middle-Mouse drag, `Space+Drag`, or explicit Hand tool (`H`).
10. **Translucent Nodes & Exact Z-Order**: Component node fill defaults to translucent (`alpha=165`, `z=20`), ports `z=40`, port pills `z=50`, hovered cables `z=60`, preview `z=65`, handles `z=70`.
11. **Safe Double-Click & View-Only Rotation**: Deduplicate clicks `< 6 px` + `250 ms` post-finish drag guard; floor editor view rotation updates `Floor.editor_view_rotation_deg` ONLY and NEVER mutates `Building.geo_anchor.rotation_deg`.
12. **Strict Wall-Locked Doors**: Open-space placement blocked; existing doors slide strictly along room/outer walls via `project_point_to_walls()` and reconcile cleanly on wall edits.
13. **HTTP-Served Map & Local-Only API Key**: Serve `map.html` via `http://127.0.0.1:<port>/assets/map.html` with `on_map_ready()` handshake & JS console forwarding; keep Google Maps API key solely in `%APPDATA%/NetVizStudio/config.json` (or `NETVIZ_GOOGLE_MAPS_API_KEY`), NEVER inside `.netviz`.
14. **Strict Foreground-Only Android**: Zero background services or permissions; `ON_PAUSE`/`ON_STOP` unconditionally halt Wi-Fi, GPS, and compass; API 33+ `RECEIVER_NOT_EXPORTED` + active polling; `CrashLogger` persists fatal traces to `last_crash.txt`.
15. **Exact Heatmap Orientation & Thread Safety**: Heatmap pixel row `0` = `max_y` (North), matching `local_m_to_scene(min_x, max_y)` on Desktop Qt and Compose Canvas identically; all heavy work (tile prefetch, IDW heatmap, PDF export, LAN Sync, Android ZIP extraction) runs strictly off the UI thread.
