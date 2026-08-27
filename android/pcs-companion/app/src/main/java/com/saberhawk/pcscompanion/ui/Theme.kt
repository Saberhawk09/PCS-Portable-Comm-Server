package com.saberhawk.pcscompanion.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF005B47),
    onPrimary = Color.White,
    primaryContainer = Color(0xFF9BF2D4),
    onPrimaryContainer = Color(0xFF002117),
    secondary = Color(0xFF49645B),
    tertiary = Color(0xFF3E6374),
    error = Color(0xFFBA1A1A),
    background = Color(0xFFF8FAF7),
    surface = Color(0xFFF8FAF7),
    surfaceVariant = Color(0xFFDCE5DF),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF80D5B9),
    onPrimary = Color(0xFF00382B),
    primaryContainer = Color(0xFF00513E),
    onPrimaryContainer = Color(0xFF9BF2D4),
    secondary = Color(0xFFB1CCC0),
    tertiary = Color(0xFFA6CDDF),
    error = Color(0xFFFFB4AB),
    background = Color(0xFF101412),
    surface = Color(0xFF101412),
    surfaceVariant = Color(0xFF404943),
)

@Composable
fun PcsCompanionTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content,
    )
}
