# Verification report — BlueDrop AI 2.0

## Passed here

- **20 Python tests**: Windows ABI structure layout and address parsing; fragmented UTF-8 Bluetooth frames; oversized/truncated/non-object frames; wrong request IDs; whole-turn context trimming; prompt size limits; terminal control-code filtering; atomic TXT save and overwrite protection; broker authentication and HTTP routes; malformed/oversized HTTP requests; request-ID conflict; queue limit; failed provider calls; durable completed jobs; interrupted-generation recovery; reconnect integration.
- **3 React Native UI tests**: start with saved settings, save/test backend flow, stop without overwriting configuration. Native Android module mocked.
- TypeScript `tsc --noEmit`.
- ESLint `npm run lint`.
- React Native CLI config/autolinking metadata resolves successfully.
- Metro builds the production Android JavaScript bundle.
- Python source compilation.

The software integration test runs the real PC chat code against connected local sockets, with a Python surrogate for the phone's forwarding logic, the actual FastAPI app through TestClient, actual SQLite persistence, and a mocked Groq provider. It intentionally drops the first Bluetooth-side response after generation, reconnects, and checks one provider invocation, one saved TXT, and persisted conversation history.

## Not verified here

- Android Gradle/Kotlin compilation, APK installation or native runtime. No compiled APK is included. Production JS bundling is not an APK build.
- Actual Windows Winsock Bluetooth transport or physical Android–PC transfer. The ABI checks run without physical hardware.
- Android Keystore, runtime permission dialogs, device battery/background behavior and OEM Bluetooth drivers.
- Live Groq API generation: no user API key was supplied; provider calls were mocked.
- VPS deployment, public DNS/TLS issuance or Docker startup on the user's host.

The source includes the two fixes learned from the previous project: `react-native.config.js` uses a valid empty object, and the bridge uses `ctx.currentActivity`.

## Run yourself

From project root:

```powershell
py -3 -m pip install -r backend\requirements-test.txt
py -3 -m unittest discover -s tests -v
cd mobile
npm ci
npm run typecheck
npm run lint
npm test -- --runInBand
cd android
.\gradlew.bat assembleRelease
```

Then follow the **real-device acceptance test** in SETUP_BN.md. A successful software test does not establish hardware interoperability or a successful APK build. These checks are the remaining handover gates on your PC/phone and deployed backend.
