package com.bluedrop

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class BrokerFailure(val retryable: Boolean, message: String) : Exception(message)

class BrokerClient(private val settings: BridgeSettings) {
    fun call(path: String, body: JSONObject? = null): JSONObject {
        val connection = URL(settings.url() + path).openConnection() as HttpURLConnection
        try {
            connection.connectTimeout = 10000
            connection.readTimeout = 15000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Authorization", "Bearer ${settings.token()}")
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("ngrok-skip-browser-warning", "true")
            if (body != null) {
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                val raw = body.toString().toByteArray(Charsets.UTF_8)
                connection.setFixedLengthStreamingMode(raw.size)
                connection.outputStream.use { it.write(raw) }
            }
            val code = connection.responseCode
            if (code !in 200..299) {
                val retry = code == 429 || code >= 500
                val message = when(code) {
                    401 -> "Backend token rejected. Stop bridge and check BRIDGE_TOKEN."
                    404 -> "Backend endpoint/job not found. Check backend URL and persistent database."
                    409 -> "Request ID conflict. Start a new request."
                    413, 422 -> "Backend rejected request size or format."
                    429 -> "Backend busy; PC will retry."
                    else -> "Backend HTTP $code. Check deployment."
                }
                throw BrokerFailure(retry, message)
            }
            val sink = java.io.ByteArrayOutputStream()
            connection.inputStream.use { input ->
                val buffer = ByteArray(8192)
                while (true) {
                    val n = input.read(buffer)
                    if (n < 0) break
                    if (sink.size() + n > 1048576) throw BrokerFailure(false, "Backend response too large")
                    sink.write(buffer, 0, n)
                }
            }
            return JSONObject(sink.toString("UTF-8"))
        } finally { connection.disconnect() }
    }
}
