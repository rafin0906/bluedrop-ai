# BlueDrop AI — Bluetooth terminal chat

Type in Windows Terminal, press Enter, and receive an AI reply through your Android phone's internet connection. The PC needs Bluetooth and Python, but no internet at runtime.

**Start with [বাংলা setup guide](docs/SETUP_BN.md).** This is a complete Android React Native source project, a standalone Windows client, and a separate deployable Groq broker. It is not a compiled APK. See [test report](docs/TEST_REPORT.md) for what has and has not been verified.

## Project folders

| Folder | Purpose |
|---|---|
| `mobile/` | React Native 0.81.5 UI + Kotlin Bluetooth foreground service + Android Gradle wrapper |
| `backend/` | FastAPI broker, Groq API integration, SQLite job store, `.env`, Docker/Caddy deployment |
| `receiver/` | Standalone `receiver.py`; native Windows Python 3.10+, no pip packages |
| `samples/` | Example UTF-8 prompts |
| `tests/` | Protocol, backend and software integration tests |
| `docs/` | Setup, architecture, API contract, test report and troubleshooting |

## How it works

1. Windows client stores a pending request ID and conversation context locally.
2. It connects to the selected phone's secure Bluetooth Classic RFCOMM service.
3. Android sends that request over HTTPS to your backend using a bridge token.
4. The backend queues the request in SQLite, then calls Groq `openai/gpt-oss-120b` using the server-only API key.
5. Android polls the job while sending progress messages to the PC.
6. The client saves the answer as UTF-8 TXT, updates conversation history, acknowledges delivery, and displays the answer.

This is application-level messaging, not PC internet tethering. No prompt or returned code is executed. The old BlueDrop file-sync app and protocol are separate; install this new app named **BlueDrop AI** (application ID `com.bluedrop.ai`).

## Quick start

Deploy `backend/`, edit `.env`, configure HTTPS. In the phone app enter the backend base URL and matching `BRIDGE_TOKEN`, select your paired PC, and tap **Save & test backend**, then **Start bridge**.

On Windows, from any directory containing just the new receiver file:

```powershell
py -3 .\receiver.py --setup
py -3 .\receiver.py --chat
```

Or use an absolute path from any working directory:

```powershell
py -3 "C:\BlueDropAI\receiver\receiver.py" --chat
```

Chat commands: `/new` clears model context; `/quit` exits; Ctrl+C exits and retains any pending request. Enter sends one line. Context is the most recent complete turns, at most 40 messages and 64 KiB combined UTF-8 text. Older turns are dropped automatically. This is reply-at-completion, not token streaming.

Optional file prompt:

```powershell
py -3 .\receiver.py --send "C:\Prompts\question.txt"
```

`--send` starts a fresh context for that prompt; subsequent interactive chat can continue its answer. Files must be nonempty UTF-8 text, at most 64 KiB (optional UTF-8 BOM accepted). Binary files are not supported.

## Build Android release on Windows

Prerequisites: Node.js 20.19.4+ (supported Node 22 LTS is suitable), JDK 17, Android SDK/platform 36, Build Tools 36.0.0, NDK 27.1.12297006; internet for dependency downloads/build.

Extract the ZIP to `C:\BlueDropAI` so `C:\BlueDropAI\mobile\package.json` exists:

```powershell
cd C:\BlueDropAI\mobile
npm ci
npm run typecheck
npm test -- --runInBand
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
cd android
.\gradlew.bat assembleRelease
```

APK: `mobile\android\app\build\outputs\apk\release\app-release.apk`. Release embeds JavaScript and runs without Metro. The included development signing key makes personal installation convenient; use your own signing key for distribution. Android namespace `com.bluedrop` and application ID `com.bluedrop.ai` intentionally differ.

## Configuration you must supply

- A valid **Groq** API key (not an OpenAI or xAI/Grok key).
- A random `BRIDGE_TOKEN`, identical on backend and phone.
- Your deployed HTTPS backend URL on the phone. It cannot be prefilled before you deploy.
- Your paired phone/PC selections and desired local reply directory.

The app contains no hardcoded server address or API key. No VPS has been deployed for you. Keys and hosting are not included.

## Verification

```powershell
py -3 -m pip install -r backend\requirements-test.txt
py -3 -m unittest discover -s tests -v
cd mobile
npm ci
npm run typecheck
npm test -- --runInBand
```

Tests mock Groq and simulate the Android transport; no paid API calls occur. See `docs/TEST_REPORT.md` before treating a software test as a real device test.

## Documentation sources

- [Groq GPT-OSS 120B model](https://console.groq.com/docs/model/openai/gpt-oss-120b)
- [Groq API reference](https://console.groq.com/docs/api-reference)
- [Android Bluetooth connections](https://developer.android.com/develop/connectivity/bluetooth/connect-bluetooth-devices)
- [Android Bluetooth permissions](https://developer.android.com/develop/connectivity/bluetooth/bt-permissions)
- [Windows Bluetooth sockets](https://learn.microsoft.com/en-us/windows/win32/bluetooth/bluetooth-and-socket)
