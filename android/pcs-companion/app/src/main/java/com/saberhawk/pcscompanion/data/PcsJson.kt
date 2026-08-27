package com.saberhawk.pcscompanion.data

import kotlinx.serialization.json.Json

object PcsJson {
    val format = Json {
        ignoreUnknownKeys = false
        explicitNulls = true
        isLenient = false
        coerceInputValues = false
    }
}
