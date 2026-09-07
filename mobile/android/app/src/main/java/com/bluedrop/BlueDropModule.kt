package com.bluedrop

import android.bluetooth.BluetoothManager
import android.content.Intent
import android.os.Build
import android.provider.Settings
import com.facebook.react.bridge.*
import java.util.concurrent.Executors

class BlueDropModule(private val ctx: ReactApplicationContext) : ReactContextBaseJavaModule(ctx) {
    private val io = Executors.newSingleThreadExecutor()
    override fun getName() = "BlueDrop"
    private fun result(p: Promise, block: () -> Any?) {
        try { p.resolve(block()) } catch (e: Exception) { p.reject("BRIDGE", e.message, e) }
    }
    @ReactMethod fun state(p: Promise) = result(p) {
        val settings = BridgeSettings(ctx)
        Arguments.createMap().apply {
            putBoolean("running", DropService.running)
            putString("status", DropService.status)
            putInt("completed", DropService.sent)
            putString("selectedPc", settings.pc())
            putString("url", settings.url())
            putBoolean("hasToken", settings.hasToken())
        }
    }
    @ReactMethod fun paired(p: Promise) = result(p) {
        val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter ?: error("Bluetooth unavailable")
        check(adapter.isEnabled) { "Turn Bluetooth on first." }
        Arguments.createArray().apply {
            adapter.bondedDevices.sortedBy { it.name ?: it.address }.forEach { device ->
                pushMap(Arguments.createMap().apply { putString("name", device.name ?: "Paired device"); putString("address", device.address) })
            }
        }
    }
    @ReactMethod fun save(url: String, token: String, address: String, p: Promise) = result(p) {
        check(!DropService.running) { "Stop the bridge before editing settings." }
        val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter ?: error("Bluetooth unavailable")
        check(adapter.bondedDevices.any { it.address.equals(address, true) }) { "Pair your PC first." }
        BridgeSettings(ctx).save(url, token, address); true
    }
    @ReactMethod fun testBackend(p: Promise) {
        io.execute { result(p) { BrokerClient(BridgeSettings(ctx)).call("/v1/config").toString() } }
    }
    @ReactMethod fun start(p: Promise) = result(p) {
        check(ctx.currentActivity != null) { "Open the app to start the bridge." }
        val settings = BridgeSettings(ctx)
        check(settings.pc().isNotEmpty() && settings.url().isNotEmpty() && settings.hasToken()) { "Save backend settings and select your PC first." }
        val adapter = ctx.getSystemService(BluetoothManager::class.java)?.adapter ?: error("Bluetooth unavailable")
        check(adapter.isEnabled) { "Turn Bluetooth on first." }
        val intent = Intent(ctx, DropService::class.java)
        DropService.running = true
        try {
            if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(intent) else ctx.startService(intent)
        } catch (e: Exception) { DropService.running = false; throw e }
        true
    }
    @ReactMethod fun stop(p: Promise) = result(p) { ctx.stopService(Intent(ctx, DropService::class.java)); true }
    @ReactMethod fun openSettings(p: Promise) = result(p) {
        ctx.startActivity(Intent(Settings.ACTION_BLUETOOTH_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true
    }
    override fun invalidate() { io.shutdown(); super.invalidate() }
}
