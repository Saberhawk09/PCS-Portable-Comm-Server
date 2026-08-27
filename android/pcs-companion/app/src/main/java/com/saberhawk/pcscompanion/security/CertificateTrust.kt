package com.saberhawk.pcscompanion.security

import android.annotation.SuppressLint
import java.io.ByteArrayInputStream
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import javax.net.ssl.X509TrustManager

data class CertificateInfo(
    val fingerprintSha256: String,
    val subject: String,
    val issuer: String,
    val notBefore: String,
    val notAfter: String,
    val identities: List<String>,
)

object CertificateInspector {
    const val MAX_CERTIFICATE_BYTES = 64 * 1024

    fun parse(bytes: ByteArray): X509Certificate {
        require(bytes.isNotEmpty() && bytes.size <= MAX_CERTIFICATE_BYTES) {
            "Certificate must be between 1 byte and 64 KiB."
        }
        val certificate = CertificateFactory.getInstance("X.509")
            .generateCertificate(ByteArrayInputStream(bytes)) as X509Certificate
        certificate.checkValidity()
        return certificate
    }

    fun inspect(bytes: ByteArray): CertificateInfo = inspect(parse(bytes))

    fun inspect(certificate: X509Certificate): CertificateInfo {
        val identities = certificate.subjectAlternativeNames.orEmpty().mapNotNull { entry ->
            val type = entry.getOrNull(0) as? Int ?: return@mapNotNull null
            val value = entry.getOrNull(1)?.toString() ?: return@mapNotNull null
            when (type) {
                2 -> "DNS:$value"
                7 -> "IP:$value"
                else -> null
            }
        }.sorted()
        require(identities.isNotEmpty()) { "Certificate does not contain a DNS or IP SAN." }
        return CertificateInfo(
            fingerprintSha256 = sha256Fingerprint(certificate.encoded),
            subject = certificate.subjectX500Principal.name,
            issuer = certificate.issuerX500Principal.name,
            notBefore = certificate.notBefore.toInstant().toString(),
            notAfter = certificate.notAfter.toInstant().toString(),
            identities = identities,
        )
    }

    fun sha256Fingerprint(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes)
        .joinToString(":") { byte -> "%02X".format(byte) }
}

// PCS ships a deployment-local self-signed leaf certificate. This manager is
// deliberately narrower than platform CA trust: it accepts only identical DER
// bytes, checks validity, and leaves hostname/SAN verification to OkHttp.
@SuppressLint("CustomX509TrustManager")
class ExactCertificateTrustManager(
    private val trustedCertificate: X509Certificate,
) : X509TrustManager {
    private val trustedDer = trustedCertificate.encoded

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("PCS Companion does not accept client certificates.")
    }

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull()
            ?: throw CertificateException("PCS server did not provide a certificate.")
        leaf.checkValidity()
        if (!MessageDigest.isEqual(leaf.encoded, trustedDer)) {
            throw CertificateException("PCS server certificate does not match the trusted certificate.")
        }
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf(trustedCertificate)
}
