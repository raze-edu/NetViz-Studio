package org.netviz.survey.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object NetVizColors {
    val Background = Color(0xFF0F172A)
    val Surface = Color(0xFF1E293B)
    val SurfaceVariant = Color(0xFF283548)
    val Border = Color(0xFF334155)
    val Primary = Color(0xFF38BDF8)
    val Secondary = Color(0xFF22C55E)
    val Warning = Color(0xFFF59E0B)
    val Error = Color(0xFFEF4444)
    val TextPrimary = Color(0xFFF8FAFC)
    val TextMuted = Color(0xFF94A3B8)
    val GpsBlue = Color(0xFF3B82F6)
    val RiserOrange = Color(0xFFF97316)
}

private val DarkCadColorScheme = darkColorScheme(
    primary = NetVizColors.Primary,
    onPrimary = Color(0xFF082F49),
    secondary = NetVizColors.Secondary,
    onSecondary = Color(0xFF052E16),
    tertiary = NetVizColors.Warning,
    background = NetVizColors.Background,
    onBackground = NetVizColors.TextPrimary,
    surface = NetVizColors.Surface,
    onSurface = NetVizColors.TextPrimary,
    surfaceVariant = NetVizColors.SurfaceVariant,
    onSurfaceVariant = NetVizColors.TextMuted,
    outline = NetVizColors.Border,
    error = NetVizColors.Error,
    onError = Color.White
)

@Composable
fun NetVizSurveyTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkCadColorScheme,
        content = content
    )
}
