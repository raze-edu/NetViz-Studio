package org.netviz.survey.model

import kotlinx.serialization.ExperimentalSerializationApi
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.util.Locale
import java.util.UUID

const val SCHEMA_VERSION: String = "2.1.0"

fun newId(): String = UUID.randomUUID().toString()

fun normalizeMac(mac: String): String =
    mac.replace(":", "")
        .replace("-", "")
        .replace(".", "")
        .replace(" ", "")
        .lowercase(Locale.ROOT)

@OptIn(ExperimentalSerializationApi::class)
val netVizJson: Json = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = true
    coerceInputValues = true
}

@OptIn(ExperimentalSerializationApi::class)
val netVizPrettyJson: Json = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = true
    coerceInputValues = true
    prettyPrint = true
}

@Serializable
enum class ComponentType(val wireValue: String) {
    @SerialName("passive_switch")
    PASSIVE_SWITCH("passive_switch"),

    @SerialName("active_switch")
    ACTIVE_SWITCH("active_switch"),

    @SerialName("access_point")
    ACCESS_POINT("access_point"),

    @SerialName("firewall")
    FIREWALL("firewall"),

    @SerialName("router")
    ROUTER("router"),

    @SerialName("lwl_converter")
    LWL_CONVERTER("lwl_converter"),

    @SerialName("powerlan_adapter")
    POWERLAN_ADAPTER("powerlan_adapter"),

    @SerialName("patch_panel")
    PATCH_PANEL("patch_panel"),

    @SerialName("device")
    DEVICE("device")
}

@Serializable
enum class PortType(val wireValue: String) {
    @SerialName("Base100")
    BASE100("Base100"),

    @SerialName("Base1000")
    BASE1000("Base1000"),

    @SerialName("Base2500")
    BASE2500("Base2500"),

    @SerialName("Base10G")
    BASE10G("Base10G"),

    @SerialName("LWL_SM")
    LWL_SM("LWL_SM"),

    @SerialName("LWL_MM")
    LWL_MM("LWL_MM"),

    @SerialName("SFP")
    SFP("SFP"),

    @SerialName("SFP_PLUS")
    SFP_PLUS("SFP_PLUS"),

    @SerialName("PowerLAN")
    POWERLAN("PowerLAN"),

    @SerialName("Coax")
    COAX("Coax"),

    @SerialName("Custom")
    CUSTOM("Custom")
}

@Serializable
enum class LinePattern(val wireValue: String) {
    @SerialName("solid")
    SOLID("solid"),

    @SerialName("dashed")
    DASHED("dashed"),

    @SerialName("dotted")
    DOTTED("dotted"),

    @SerialName("dash_dot")
    DASH_DOT("dash_dot")
}

@Serializable
enum class PositionSource(val wireValue: String) {
    @SerialName("manual")
    MANUAL("manual"),

    @SerialName("gps")
    GPS("gps"),

    @SerialName("gps_calibrated")
    GPS_CALIBRATED("gps_calibrated")
}

@Serializable
data class Point2D(
    @SerialName("x") val x: Double = 0.0,
    @SerialName("y") val y: Double = 0.0
)

@Serializable
data class PortSpec(
    @SerialName("label") val label: String = "",
    @SerialName("port_type") val portType: PortType = PortType.BASE1000,
    @SerialName("custom_type_name") val customTypeName: String? = null,
    @SerialName("speed_mbps") val speedMbps: Int? = 1000,
    @SerialName("poe_capable") val poeCapable: Boolean = false
)

@Serializable
data class Port(
    @SerialName("id") val id: String = newId(),
    @SerialName("label") val label: String = "",
    @SerialName("port_type") val portType: PortType = PortType.BASE1000,
    @SerialName("custom_type_name") val customTypeName: String? = null,
    @SerialName("speed_mbps") val speedMbps: Int? = 1000,
    @SerialName("poe_capable") val poeCapable: Boolean = false
)

@Serializable
data class ComponentTemplate(
    @SerialName("id") val id: String = newId(),
    @SerialName("template_name") val templateName: String = "Template",
    @SerialName("component_type") val componentType: ComponentType,
    @SerialName("category") val category: String = "Custom",
    @SerialName("description") val description: String = "",
    @SerialName("default_name_prefix") val defaultNamePrefix: String = "DEV",
    @SerialName("accent_color_hex") val accentColorHex: String? = null,
    @SerialName("default_frequencies_ghz") val defaultFrequenciesGhz: List<Double> = emptyList(),
    @SerialName("default_ssids") val defaultSsids: List<String> = emptyList(),
    @SerialName("default_custom_attributes") val defaultCustomAttributes: Map<String, String> = emptyMap(),
    @SerialName("port_specs") val portSpecs: List<PortSpec> = emptyList(),
    @SerialName("is_builtin") val isBuiltin: Boolean = false
)

@Serializable
data class Component(
    @SerialName("id") val id: String = newId(),
    @SerialName("component_type") val componentType: ComponentType,
    @SerialName("name") val name: String = "",
    @SerialName("template_id") val templateId: String? = null,
    @SerialName("ip_address") val ipAddress: String = "",
    @SerialName("mac_address") val macAddress: String = "",
    @SerialName("frequencies_ghz") val frequenciesGhz: List<Double> = emptyList(),
    @SerialName("ssids") val ssids: List<String> = emptyList(),
    @SerialName("custom_attributes") val customAttributes: Map<String, String> = emptyMap(),
    @SerialName("ports") val ports: List<Port> = emptyList(),
    @SerialName("logical_pos") val logicalPos: Point2D = Point2D(0.0, 0.0),
    @SerialName("physical_building_id") val physicalBuildingId: String? = null,
    @SerialName("physical_floor_id") val physicalFloorId: String? = null,
    @SerialName("physical_pos_m") val physicalPosM: Point2D? = null
)

@Serializable
data class CableStyle(
    @SerialName("color_hex") val colorHex: String = "#38BDF8",
    @SerialName("pattern") val pattern: LinePattern = LinePattern.SOLID,
    @SerialName("width_px") val widthPx: Double = 2.5
)

@Serializable
data class PhysicalCableSegment(
    @SerialName("floor_id") val floorId: String = "",
    @SerialName("waypoints_m") val waypointsM: List<Point2D> = emptyList(),
    @SerialName("start_hole_id") val startHoleId: String? = null,
    @SerialName("end_hole_id") val endHoleId: String? = null
)

@Serializable
data class Cable(
    @SerialName("id") val id: String = newId(),
    @SerialName("source_component_id") val sourceComponentId: String = "",
    @SerialName("source_port_id") val sourcePortId: String = "",
    @SerialName("target_component_id") val targetComponentId: String = "",
    @SerialName("target_port_id") val targetPortId: String = "",
    @SerialName("label") val label: String = "",
    @SerialName("vlan_id") val vlanId: String = "",
    @SerialName("style") val style: CableStyle = CableStyle(),
    @SerialName("logical_waypoints") val logicalWaypoints: List<Point2D> = emptyList(),
    @SerialName("logical_bundle_id") val logicalBundleId: String? = null,
    @SerialName("physical_segments") val physicalSegments: List<PhysicalCableSegment> = emptyList(),
    @SerialName("physical_bundle_id") val physicalBundleId: String? = null
)

@Serializable
data class CableBundle(
    @SerialName("id") val id: String = newId(),
    @SerialName("entry_point") val entryPoint: Point2D = Point2D(0.0, 0.0),
    @SerialName("exit_point") val exitPoint: Point2D = Point2D(0.0, 0.0),
    @SerialName("name") val name: String = "Cable Bundle",
    @SerialName("scope") val scope: String = "logical",
    @SerialName("floor_id") val floorId: String? = null,
    @SerialName("waypoints") val waypoints: List<Point2D> = emptyList(),
    @SerialName("member_cable_ids") val memberCableIds: List<String> = emptyList(),
    @SerialName("style") val style: CableStyle = CableStyle(colorHex = "#F59E0B", widthPx = 5.5),
    @SerialName("collapsed") val collapsed: Boolean = true
)

@Serializable
data class GeoAnchor(
    @SerialName("origin_lat") val originLat: Double,
    @SerialName("origin_lon") val originLon: Double,
    @SerialName("rotation_deg") val rotationDeg: Double = 0.0
)

@Serializable
data class BlueprintUnderlay(
    @SerialName("archive_path") val archivePath: String = "",
    @SerialName("meters_per_pixel") val metersPerPixel: Double = 0.02,
    @SerialName("offset_m") val offsetM: Point2D = Point2D(0.0, 0.0),
    @SerialName("rotation_deg") val rotationDeg: Double = 0.0,
    @SerialName("opacity") val opacity: Double = 0.45,
    @SerialName("visible") val visible: Boolean = true
)

@Serializable
data class Room(
    @SerialName("id") val id: String = newId(),
    @SerialName("name") val name: String = "",
    @SerialName("polygon_m") val polygonM: List<Point2D> = emptyList(),
    @SerialName("fill_color_hex") val fillColorHex: String = "#1E293B",
    @SerialName("wall_attenuation_db") val wallAttenuationDb: Double = 6.0
)

@Serializable
data class Door(
    @SerialName("id") val id: String = newId(),
    @SerialName("position_m") val positionM: Point2D = Point2D(0.0, 0.0),
    @SerialName("angle_deg") val angleDeg: Double = 0.0,
    @SerialName("width_m") val widthM: Double = 0.9,
    @SerialName("swing_flip") val swingFlip: Boolean = false,
    @SerialName("hinge_flip") val hingeFlip: Boolean = false
)

@Serializable
data class Stairs(
    @SerialName("id") val id: String = newId(),
    @SerialName("polygon_m") val polygonM: List<Point2D> = emptyList(),
    @SerialName("label") val label: String = "Stairs",
    @SerialName("direction_deg") val directionDeg: Double = 0.0
)

@Serializable
data class VerticalHole(
    @SerialName("id") val id: String = newId(),
    @SerialName("position_m") val positionM: Point2D = Point2D(0.0, 0.0),
    @SerialName("riser_group_id") val riserGroupId: String = "",
    @SerialName("label") val label: String = "Riser",
    @SerialName("radius_m") val radiusM: Double = 0.30
)

@Serializable
data class Floor(
    @SerialName("id") val id: String = newId(),
    @SerialName("name") val name: String = "",
    @SerialName("outer_polygon_m") val outerPolygonM: List<Point2D> = emptyList(),
    @SerialName("level_index") val levelIndex: Int = 0,
    @SerialName("editor_view_rotation_deg") val editorViewRotationDeg: Double = 0.0,
    @SerialName("blueprint") val blueprint: BlueprintUnderlay? = null,
    @SerialName("rooms") val rooms: List<Room> = emptyList(),
    @SerialName("doors") val doors: List<Door> = emptyList(),
    @SerialName("stairs") val stairs: List<Stairs> = emptyList(),
    @SerialName("holes") val holes: List<VerticalHole> = emptyList()
)

@Serializable
data class Building(
    @SerialName("id") val id: String = newId(),
    @SerialName("name") val name: String = "",
    @SerialName("geo_anchor") val geoAnchor: GeoAnchor? = null,
    @SerialName("floor_height_m") val floorHeightM: Double = 3.2,
    @SerialName("footprint_latlon") val footprintLatlon: List<List<Double>> = emptyList(),
    @SerialName("floors") val floors: List<Floor> = emptyList()
)

@Serializable
data class WifiReading(
    @SerialName("ssid") val ssid: String = "",
    @SerialName("bssid") val bssid: String = "",
    @SerialName("rssi_dbm") val rssiDbm: Int,
    @SerialName("frequency_mhz") val frequencyMhz: Int
)

@Serializable
data class SurveySample(
    @SerialName("id") val id: String = newId(),
    @SerialName("timestamp_utc") val timestampUtc: String = "",
    @SerialName("building_id") val buildingId: String = "",
    @SerialName("floor_id") val floorId: String = "",
    @SerialName("pos_m") val posM: Point2D = Point2D(0.0, 0.0),
    @SerialName("source") val source: PositionSource = PositionSource.MANUAL,
    @SerialName("gps_lat") val gpsLat: Double? = null,
    @SerialName("gps_lon") val gpsLon: Double? = null,
    @SerialName("gps_accuracy_m") val gpsAccuracyM: Double? = null,
    @SerialName("calibration_offset_m") val calibrationOffsetM: Point2D? = null,
    @SerialName("readings") val readings: List<WifiReading> = emptyList()
)

@Serializable
data class SurveySession(
    @SerialName("id") val id: String = newId(),
    @SerialName("name") val name: String = "Survey Session",
    @SerialName("created_utc") val createdUtc: String = "",
    @SerialName("device_model") val deviceModel: String = "",
    @SerialName("samples") val samples: List<SurveySample> = emptyList()
) {
    fun toJson(pretty: Boolean = true): String =
        if (pretty) netVizPrettyJson.encodeToString(this) else netVizJson.encodeToString(this)

    companion object {
        fun fromJson(text: String): SurveySession = netVizJson.decodeFromString(text)
    }
}

@Serializable
data class MapPreferences(
    @SerialName("preferred_provider") val preferredProvider: String = "google",
    @SerialName("offline_cache_margin_m") val offlineCacheMarginM: Double = 250.0,
    @SerialName("offline_cache_zooms") val offlineCacheZooms: List<Int> = listOf(16, 17, 18, 19)
)

@Serializable
data class Project(
    @SerialName("schema_version") val schemaVersion: String = SCHEMA_VERSION,
    @SerialName("id") val id: String = newId(),
    @SerialName("name") val name: String = "Untitled Network",
    @SerialName("custom_templates") val customTemplates: List<ComponentTemplate> = emptyList(),
    @SerialName("components") val components: List<Component> = emptyList(),
    @SerialName("cables") val cables: List<Cable> = emptyList(),
    @SerialName("bundles") val bundles: List<CableBundle> = emptyList(),
    @SerialName("buildings") val buildings: List<Building> = emptyList(),
    @SerialName("surveys") val surveys: List<SurveySession> = emptyList(),
    @SerialName("map_preferences") val mapPreferences: MapPreferences = MapPreferences()
) {
    fun toJson(pretty: Boolean = true): String =
        if (pretty) netVizPrettyJson.encodeToString(this) else netVizJson.encodeToString(this)

    fun getComponent(componentId: String): Component? =
        components.firstOrNull { it.id == componentId }

    fun getPort(componentId: String, portId: String): Port? =
        getComponent(componentId)?.ports?.firstOrNull { it.id == portId }

    fun getTargetSsids(): List<String> =
        components
            .asSequence()
            .flatMap { it.ssids.asSequence() }
            .map { it.trim() }
            .filter { it.isNotEmpty() }
            .distinct()
            .sorted()
            .toList()

    fun isPortOccupied(
        componentId: String,
        portId: String,
        excludeCableId: String? = null
    ): Boolean = cables.any { cable ->
        if (excludeCableId != null && cable.id == excludeCableId) {
            return@any false
        }
        (cable.sourceComponentId == componentId && cable.sourcePortId == portId) ||
            (cable.targetComponentId == componentId && cable.targetPortId == portId)
    }

    fun getPortConnection(
        componentId: String,
        portId: String
    ): Triple<Cable, Component, Port>? {
        for (cable in cables) {
            val peerCompId: String
            val peerPortId: String
            when {
                cable.sourceComponentId == componentId && cable.sourcePortId == portId -> {
                    peerCompId = cable.targetComponentId
                    peerPortId = cable.targetPortId
                }
                cable.targetComponentId == componentId && cable.targetPortId == portId -> {
                    peerCompId = cable.sourceComponentId
                    peerPortId = cable.sourcePortId
                }
                else -> continue
            }
            val peerComp = getComponent(peerCompId) ?: return null
            val peerPort = getPort(peerCompId, peerPortId) ?: return null
            return Triple(cable, peerComp, peerPort)
        }
        return null
    }

    fun getBuilding(buildingId: String): Building? =
        buildings.firstOrNull { it.id == buildingId }

    fun getFloor(buildingId: String, floorId: String): Floor? =
        getBuilding(buildingId)?.floors?.firstOrNull { it.id == floorId }

    fun findComponentByMac(bssid: String): Component? {
        val target = normalizeMac(bssid)
        if (target.isEmpty()) return null
        return components.firstOrNull { normalizeMac(it.macAddress) == target }
    }

    companion object {
        fun fromJson(text: String): Project = netVizJson.decodeFromString(text)
    }
}
