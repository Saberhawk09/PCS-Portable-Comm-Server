package com.saberhawk.pcscompanion.security

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.util.Base64
import androidx.annotation.RequiresApi
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

// Credential writes are synchronous by design: pairing must not report success
// until encrypted token bytes are durably accepted by SharedPreferences.
@SuppressLint("ApplySharedPref", "UseKtx")
class SecureTokenStore(context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES_NAME, Context.MODE_PRIVATE)

    fun hasToken(): Boolean = preferences.contains(KEY_CIPHERTEXT) && preferences.contains(KEY_IV)

    fun save(token: String) {
        require(token.isNotBlank()) { "Pairing token was empty." }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        val ciphertext = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        check(preferences.edit()
            .putString(KEY_CIPHERTEXT, Base64.encodeToString(ciphertext, Base64.NO_WRAP))
            .putString(KEY_IV, Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .commit()) { "Unable to persist the encrypted PCS token." }
    }

    fun load(): String? {
        val ciphertext = preferences.getString(KEY_CIPHERTEXT, null) ?: return null
        val iv = preferences.getString(KEY_IV, null) ?: return null
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(
                Cipher.DECRYPT_MODE,
                getExistingKey() ?: error("Android Keystore key is unavailable."),
                GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)),
            )
            cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)).toString(Charsets.UTF_8)
        }.getOrElse {
            clear(deleteKey = false)
            null
        }
    }

    fun clear(deleteKey: Boolean = false) {
        check(preferences.edit().clear().commit()) { "Unable to clear the PCS token." }
        if (deleteKey) {
            val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
            if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS)
        }
    }

    private fun getExistingKey(): SecretKey? {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        return keyStore.getKey(KEY_ALIAS, null) as? SecretKey
    }

    private fun getOrCreateKey(): SecretKey = getExistingKey() ?: generateKey(preferStrongBox = true)

    private fun generateKey(preferStrongBox: Boolean): SecretKey {
        if (preferStrongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            return generateStrongBoxKey()
        }
        return generateStandardKey()
    }

    @RequiresApi(Build.VERSION_CODES.P)
    private fun generateStrongBoxKey(): SecretKey = try {
        generateKeyWithSpec(strongBox = true)
    } catch (_: StrongBoxUnavailableException) {
        generateStandardKey()
    }

    private fun generateStandardKey(): SecretKey = generateKeyWithSpec(strongBox = false)

    private fun generateKeyWithSpec(strongBox: Boolean): SecretKey {
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        val builder = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .setRandomizedEncryptionRequired(true)
        if (strongBox && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            builder.setIsStrongBoxBacked(true)
        }
        generator.init(builder.build())
        return generator.generateKey()
    }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_ALIAS = "pcs_companion_device_token_v1"
        const val PREFERENCES_NAME = "pcs_credentials"
        const val KEY_CIPHERTEXT = "device_token_ciphertext"
        const val KEY_IV = "device_token_iv"
    }
}
