package com.bluedrop

import android.content.Context
import android.util.Base64
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Token encrypted with an app-private Android Keystore key; backups are disabled. */
class BridgeSettings(context: Context) {
    private val prefs = context.getSharedPreferences("ai_bridge", Context.MODE_PRIVATE)
    fun pc() = prefs.getString("pc", "") ?: ""
    fun url() = prefs.getString("url", "") ?: ""
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("bridge_token", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("bridge_token", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun token(): String {
        val saved = prefs.getString("token", null) ?: return ""
        val parts = saved.split(":")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)))
        return String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8)
    }
    fun hasToken() = prefs.contains("token")
    fun save(urlText: String, tokenText: String, pcText: String) {
        val normalized = urlText.trim().trimEnd('/')
        val parsed = URL(normalized)
        require((parsed.protocol == "https" || parsed.protocol == "http") && parsed.host.isNotEmpty() && parsed.userInfo == null && parsed.query == null && parsed.ref == null) { "Use your backend HTTP or HTTPS URL, without query parameters." }
        require(Regex("[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}").matches(pcText)) { "Choose your paired PC." }
        val edit = prefs.edit().putString("url", normalized).putString("pc", pcText.uppercase())
        if (tokenText.isNotBlank()) {
            require(tokenText.length >= 32 && tokenText.all { it.code in 33..126 } && !tokenText.startsWith("CHANGE_")) { "Use a random ASCII bridge token of at least 32 characters." }
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            val encrypted = cipher.doFinal(tokenText.toByteArray(Charsets.UTF_8))
            edit.putString("token", Base64.encodeToString(cipher.iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(encrypted, Base64.NO_WRAP))
        } else require(hasToken()) { "Enter your backend BRIDGE_TOKEN." }
        check(edit.commit()) { "Could not save settings" }
    }
}
