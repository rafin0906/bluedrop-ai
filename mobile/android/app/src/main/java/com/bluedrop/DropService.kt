package com.bluedrop

import android.Manifest
import android.app.*
import android.bluetooth.*
import android.content.*
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.*
import org.json.JSONObject
import java.io.*
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** One selected, bonded PC; secure RFCOMM chat frames relayed to an HTTPS broker. */
class DropService : Service() {
    companion object {
        const val UUID_TEXT = "c91c315b-8c0b-487f-a640-c073e9415d55"
        @Volatile var running = false
        @Volatile var status = "Stopped"
        @Volatile var progress = 0.0
        @Volatile var sent = 0
    }
    @Volatile private var active = false
    @Volatile private var listener: BluetoothServerSocket? = null
    @Volatile private var client: BluetoothSocket? = null
    private val deadlines = Executors.newSingleThreadScheduledExecutor()
    private var worker: Thread? = null
    private val adapter: BluetoothAdapter?
        get() = getSystemService(BluetoothManager::class.java)?.adapter

    override fun onBind(intent: Intent?) = null
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == "STOP") { stopSelf(); return START_NOT_STICKY }
        if (active) return START_NOT_STICKY
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(
            NotificationChannel("transfer", "Bluetooth transfers", NotificationManager.IMPORTANCE_LOW))
        val launch = PendingIntent.getActivity(this, 1, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val stop = PendingIntent.getService(this, 2, Intent(this, DropService::class.java).setAction("STOP"), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, "transfer") else Notification.Builder(this)
        val notice = builder.setContentTitle("BlueDrop AI bridge")
            .setContentText("Waiting for your selected PC • tap to manage")
            .setSmallIcon(android.R.drawable.stat_sys_upload).setOngoing(true)
            .setContentIntent(launch).addAction(android.R.drawable.ic_media_pause, "Stop", stop).build()
        try {
            if (Build.VERSION.SDK_INT >= 31 && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) error("Bluetooth permission is required.")
            check(adapter?.isEnabled == true) { "Turn Bluetooth on, then start again." }
            check(BridgeSettings(this).pc().isNotEmpty()) { "Choose a paired PC first." }
            if (Build.VERSION.SDK_INT >= 29) startForeground(7, notice, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
            else startForeground(7, notice)
            active = true; running = true; status = "Starting server…"; progress = 0.0
            worker = Thread({ serve() }, "BlueDrop-RFCOMM").apply { start() }
        } catch (e: Exception) { status = e.message ?: "Cannot start server"; running = false; stopSelf() }
        // User must start explicitly after reboot, force-stop, or process termination.
        return START_NOT_STICKY
    }
    private fun serve() {
        try {
            val store = BridgeSettings(this)
            val socket = adapter!!.listenUsingRfcommWithServiceRecord("BlueDropAI", UUID.fromString(UUID_TEXT))
            listener = socket
            // onDestroy may have run while Android was creating the listening socket.
            if (!active) { socket.close(); return }
            while (active) {
                status = "Ready • waiting for your PC"
                val peer = socket.accept()
                client = peer
                if (!active) { peer.close(); break }
                try {
                    check(peer.remoteDevice.bondState == BluetoothDevice.BOND_BONDED &&
                        peer.remoteDevice.address.equals(store.pc(), true)) { "Only the selected paired PC can use this bridge." }
                    status = "PC connected"; progress = 0.0
                    val wake = getSystemService(PowerManager::class.java)
                        .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BlueDrop:Transfer")
                    wake.acquire(31 * 60 * 1000L)
                    try { transfer(peer, store) } finally { if (wake.isHeld) wake.release() }
                    if (active) status = "Ready • waiting for your next message"
                } catch (e: Exception) {
                    if (active) status = "Connection ended: ${e.message ?: "Bluetooth error"}"
                } finally { try { peer.close() } catch (_: Exception) {}; client = null }
            }
        } catch (e: Exception) { if (active) status = "Server stopped: ${e.message ?: "Bluetooth unavailable"}" }
        finally {
            try { listener?.close() } catch (_: Exception) {}
            listener = null
            if (active) { running = false; active = false; stopSelf() }
        }
    }
    // A deadline closes the Bluetooth socket to interrupt a stalled blocking stream operation.
    private fun <T> timed(peer: BluetoothSocket, seconds: Long, block: () -> T): T {
        val timer = deadlines.schedule({ try { peer.close() } catch (_: Exception) {} }, seconds, TimeUnit.SECONDS)
        try { return block() } finally { timer.cancel(false) }
    }
    private fun read(input: DataInputStream): JSONObject {
        val n = input.readInt(); require(n in 1..1048576) { "Invalid frame length" }
        val bytes = ByteArray(n); input.readFully(bytes)
        return JSONObject(String(bytes, Charsets.UTF_8))
    }
    private fun write(output: DataOutputStream, message: JSONObject) {
        val data = message.toString().toByteArray(Charsets.UTF_8)
        require(data.size in 1..1048576)
        output.writeInt(data.size); output.write(data); output.flush()
    }
    private fun transfer(peer: BluetoothSocket, store: BridgeSettings) {
        val input = DataInputStream(BufferedInputStream(peer.inputStream))
        val output = DataOutputStream(BufferedOutputStream(peer.outputStream))
        val request = timed(peer, 30) { read(input) }
        require(request.getString("type") == "chat" && request.getInt("version") == 2) { "Use the BlueDrop AI receiver (protocol 2)." }
        val id = request.getString("id")
        require(UUID.fromString(id).toString() == id) { "Invalid request ID" }
        val messages = request.getJSONArray("messages")
        require(messages.length() in 1..40) { "Too many messages" }
        var bytes = 0
        for (i in 0 until messages.length()) {
            val m = messages.getJSONObject(i)
            require(m.getString("role") in listOf("user", "assistant"))
            val text = m.getString("content")
            require(text.isNotBlank())
            bytes += text.toByteArray(Charsets.UTF_8).size
        }
        require(bytes <= 65536 && messages.getJSONObject(messages.length()-1).getString("role") == "user")
        fun emit(message: JSONObject) {
            timed(peer, 30) { write(output, message.put("id", id)) }
        }
        emit(JSONObject().put("type", "status").put("status", "forwarding"))
        try {
            val broker = BrokerClient(store)
            status = "PC connected • contacting backend"
            var job = broker.call("/v1/jobs", JSONObject().put("id", id).put("messages", messages))
            val until = android.os.SystemClock.elapsedRealtime() + 15 * 60 * 1000L
            while (active) {
                require(job.getString("id") == id) { "Backend returned the wrong request" }
                when (job.getString("status")) {
                    "completed" -> {
                        val answer = job.getString("answer")
                        require(answer.toByteArray(Charsets.UTF_8).size <= 204800)
                        status = "Sending AI reply to PC"
                        emit(JSONObject().put("type", "result").put("answer", answer)
                            .put("finish_reason", job.optString("finish_reason", "stop")))
                        val ack = timed(peer, 30) { read(input) }
                        require(ack.optString("type") == "ack" && ack.optString("id") == id)
                        sent++; status = "Reply saved on PC"; return
                    }
                    "failed" -> {
                        emit(JSONObject().put("type", "error").put("retryable", false)
                            .put("message", job.optString("error", "Generation failed")))
                        return
                    }
                    "queued", "running" -> {
                        status = "AI ${job.getString("status")} • waiting for reply"
                        emit(JSONObject().put("type", "status").put("status", job.getString("status")))
                    }
                    else -> error("Unknown backend status")
                }
                if (android.os.SystemClock.elapsedRealtime() > until) throw BrokerFailure(true, "Still waiting; PC will reconnect to the same request.")
                Thread.sleep(3000)
                job = broker.call("/v1/jobs/$id")
            }
        } catch (e: Exception) {
            if (!active) return
            val retry = if (e is BrokerFailure) e.retryable else e is java.io.IOException
            val message = if (e is BrokerFailure) e.message else if (retry) "Phone could not reach backend. Check mobile internet; PC will retry." else "Invalid backend response. Check deployment."
            status = message ?: "Bridge error"
            emit(JSONObject().put("type", "error").put("retryable", retry).put("message", status))
        }
    }
    override fun onDestroy() {
        val wasActive = active
        active = false; running = false
        if (wasActive) status = "Stopped"
        try { client?.close() } catch (_: Exception) {}
        try { listener?.close() } catch (_: Exception) {}
        deadlines.shutdownNow(); worker?.interrupt()
        super.onDestroy()
    }
}
