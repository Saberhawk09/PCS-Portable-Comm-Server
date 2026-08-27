package com.saberhawk.pcscompanion

import android.app.Activity
import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.fragment.app.FragmentActivity
import com.saberhawk.pcscompanion.ui.PcsCompanionApp
import com.saberhawk.pcscompanion.ui.PcsCompanionTheme

class MainActivity : FragmentActivity() {
    private var credentialSuccess: (() -> Unit)? = null
    private var credentialError: ((String) -> Unit)? = null
    private val credentialLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        val onSuccess = credentialSuccess
        val onError = credentialError
        credentialSuccess = null
        credentialError = null
        if (result.resultCode == Activity.RESULT_OK) {
            onSuccess?.invoke()
        } else {
            onError?.invoke("Device-credential confirmation was canceled.")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            PcsCompanionTheme {
                PcsCompanionApp(
                    authenticateAction = ::authenticateAction,
                )
            }
        }
    }

    private fun authenticateAction(
        actionLabel: String,
        onAuthenticated: () -> Unit,
        onError: (String) -> Unit,
    ) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) {
            authenticateBeforeAndroid11(actionLabel, onAuthenticated, onError)
            return
        }
        val authenticators = BiometricManager.Authenticators.BIOMETRIC_STRONG or
            BiometricManager.Authenticators.DEVICE_CREDENTIAL
        val availability = BiometricManager.from(this).canAuthenticate(authenticators)
        if (availability != BiometricManager.BIOMETRIC_SUCCESS) {
            onError("Configure a secure device credential or strong biometric before running PCS changes.")
            return
        }
        val prompt = BiometricPrompt(
            this,
            ContextCompat.getMainExecutor(this),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onAuthenticated()
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    onError(errString.toString())
                }
            },
        )
        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Authorize PCS change")
            .setSubtitle(actionLabel)
            .setAllowedAuthenticators(authenticators)
            .build()
        prompt.authenticate(promptInfo)
    }

    private fun authenticateBeforeAndroid11(
        actionLabel: String,
        onAuthenticated: () -> Unit,
        onError: (String) -> Unit,
    ) {
        val biometricAvailable = BiometricManager.from(this)
            .canAuthenticate(BiometricManager.Authenticators.BIOMETRIC_STRONG) ==
            BiometricManager.BIOMETRIC_SUCCESS
        val keyguard = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        if (!biometricAvailable) {
            if (keyguard.isDeviceSecure) {
                launchDeviceCredential(actionLabel, onAuthenticated, onError)
            } else {
                onError("Configure a secure device credential or strong biometric before running PCS changes.")
            }
            return
        }
        val prompt = BiometricPrompt(
            this,
            ContextCompat.getMainExecutor(this),
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    onAuthenticated()
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    if (errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON && keyguard.isDeviceSecure) {
                        launchDeviceCredential(actionLabel, onAuthenticated, onError)
                    } else {
                        onError(errString.toString())
                    }
                }
            },
        )
        val promptInfo = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Authorize PCS change")
            .setSubtitle(actionLabel)
            .setAllowedAuthenticators(BiometricManager.Authenticators.BIOMETRIC_STRONG)
            .setNegativeButtonText(if (keyguard.isDeviceSecure) "Use screen lock" else "Cancel")
            .build()
        prompt.authenticate(promptInfo)
    }

    @Suppress("DEPRECATION")
    private fun launchDeviceCredential(
        actionLabel: String,
        onAuthenticated: () -> Unit,
        onError: (String) -> Unit,
    ) {
        if (credentialSuccess != null) {
            onError("Another device-credential request is already active.")
            return
        }
        val keyguard = getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
        val intent = keyguard.createConfirmDeviceCredentialIntent("Authorize PCS change", actionLabel)
        if (intent == null) {
            onError("Configure a secure device credential before running PCS changes.")
            return
        }
        credentialSuccess = onAuthenticated
        credentialError = onError
        credentialLauncher.launch(intent)
    }
}
