# BlueDrop AI চালানোর সম্পূর্ণ নির্দেশনা

আপনার PC offline থাকতে পারবে। ফোনে Bluetooth + mobile data অথবা Wi-Fi internet থাকতে হবে। PC-তে native Windows Python চলবে, WSL নয়। প্রথমবার APK build/Python install করার জন্য internet অন্য কোনোভাবে লাগতে পারে; প্রতিদিন chat করার সময় PC internet লাগবে না।

## ১. ZIP extract এবং প্রয়োজনীয় জিনিস

ZIP-এর `BlueDropAI` folder-টি `C:\BlueDropAI` হিসেবে রাখুন। ভিতরে `mobile`, `backend`, `receiver` আছে। পুরোনো BlueDrop folder-এর উপর overwrite করবেন না। নতুন APK আলাদা BlueDrop AI app হিসেবে install হবে।

**শুধু chat চালানোর PC:** Windows 10/11, Bluetooth Classic/RFCOMM সমর্থিত adapter ও driver, Python 3.10 বা পরের version। Python আগে install করা থাকতে হবে। এখানে `npm`, React Native source বা pip dependency লাগবে না। `receiver.py` একাই copy করা যায়।

**APK build করার PC:** Node 20.19.4+ বা Node 22 LTS, JDK 17, Android Studio/SDK, SDK Platform 36, Android SDK Build-Tools 36.0.0, Android NDK 27.1.12297006। Android Studio → SDK Manager দিয়ে এগুলো install করুন। SDK Command-line Tools ও Platform-Tools-ও রাখুন। SDK licence accept করতে SDK Manager ব্যবহার করতে পারেন। JAVA_HOME অবশ্যই JDK 17 folder হবে।

**ফোন:** Android 7.0/API 24 বা নতুন, Bluetooth Classic, কাজ করে এমন internet connection। Android 12+ এ Nearby devices permission Allow করবেন; Android 13+ এ notification permission দিলে bridge notification দেখা যাবে।

**Backend host:** Python 3.10+ ও persistent writable disk, অথবা Docker Compose-সহ VPS। Valid Groq key, model access, এবং public HTTPS endpoint লাগবে।

## ২. Backend প্রস্তুত করুন

`backend/.env` তৈরি করা আছে। সেখানে এই তিনটি বিষয় বদলান:

```dotenv
GROQ_API_KEY=your_actual_groq_key
BRIDGE_TOKEN=your_random_long_token
GROQ_MODEL=openai/gpt-oss-120b
DATABASE_PATH=./data/jobs.sqlite3
DOMAIN=ai.yourdomain.com
```

Random token বানানোর command:

```powershell
py -3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Linux/VPS-এ `python3` ব্যবহার করুন। এই token ফোনের **Bridge token** field-এও দিতে হবে। **Groq API key ফোনে দেবেন না।** Groq আর xAI/Grok আলাদা service; এখানে Groq Console-এর key লাগবে।

### VPS-এ Docker দিয়ে HTTPS-সহ deploy

VPS-এ শুধু `backend/` folder copy করলেই হবে। Domain-এর DNS A record VPS public IP-তে দিন; AAAA থাকলে সেটিও ঠিক হতে হবে। Firewall-এ port 80 ও 443 খোলা থাকতে হবে। অন্য service আগে ওই port ব্যবহার করলে Caddy config আপনার existing reverse proxy অনুযায়ী সাজাতে হবে।

```bash
cd /path/to/backend
# .env সম্পাদনা করুন
chmod 600 .env
docker compose up -d --build
docker compose logs --tail=80 broker
docker compose logs --tail=80 caddy
```

Caddy domain-এর TLS certificate নেবে। Browser-এ `https://ai.yourdomain.com/healthz` খুললে `status: ok` আসবে। ফোনের **Save & test backend** token এবং config endpoint-ও যাচাই করবে। এই test Groq-কে generation request করে না; প্রথম chat key/quota/model access যাচাই করবে।

Job database Docker named volume `jobs`-এ থাকে। Container recreate করলে থাকে। `docker compose down -v` দিলে সেই database মুছে যাবে—retry/recovery-এর জন্য এটি দরকার। একটি broker replica এবং **একটি Uvicorn worker** চালাবেন।

### Docker ছাড়া Python deployment

```bash
cd /path/to/backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn broker:app --host 127.0.0.1 --port 8000 --workers 1
```

এটি local HTTP listener। VPS-এ Caddy/Nginx দিয়ে public HTTPS দিন এবং process manager দিয়ে process running রাখুন। ফোনে `http://` বা `localhost` দেবেন না। ফোনে localhost মানে ফোন নিজেই। কোনো managed host ব্যবহার করলে persistent disk-এ DATABASE_PATH দিন; ephemeral/serverless filesystem এই job store-এর জন্য উপযুক্ত নয়। Host-এর assigned port অনুযায়ী Uvicorn port সেট করতে হবে।

## ৩. Android APK build

PowerShell:

```powershell
cd C:\BlueDropAI\mobile
node --version
java -version
npm ci
npm run typecheck
npm test -- --runInBand
$env:ANDROID_HOME = "$env:LOCALAPPDATA\Android\Sdk"
cd android
.\gradlew.bat assembleRelease
```

SDK অন্য জায়গায় থাকলে ANDROID_HOME-এ সেই actual folder দিন। বিকল্পভাবে `mobile/android/local.properties` নিজে তৈরি করুন:

```properties
sdk.dir=C:/Users/sohel/AppData/Local/Android/Sdk
```

এই machine-specific file ZIP-এ নেই। `JAVA_HOME` ভুল থাকলে আপনার actual JDK 17 path দিন, তারপর নতুন terminal থেকে `java -version` দেখুন।

APK পাওয়া যাবে:

```text
C:\BlueDropAI\mobile\android\app\build\outputs\apk\release\app-release.apk
```

ফোনে APK copy করে install করুন। Installer-এর প্রয়োজন হলে Install unknown apps permission দিন। Release APK-তে Metro লাগে না। Debug APK সাধারণত Metro লাগে, তাই এই কাজে Release ব্যবহার করুন।

Included signing key development key; ব্যক্তিগত পরীক্ষার জন্য। Public release করলে নিজের keystore দিন। UI React Native; Bluetooth/HTTPS service Kotlin-এ, যাতে JS background suspension হলে transfer সঙ্গে সঙ্গে বন্ধ না হয়ে যায়।

## ৪. Phone ও PC pair করুন

দুই device-এ Bluetooth on করুন। Windows Settings → Bluetooth & devices → Add device → Bluetooth → আপনার ফোন। দুই পাশে pairing code মিলিয়ে confirm করুন।

**Windows-এর Receive files window খুলতে হবে না।** এটি custom Bluetooth connection; Python client নিজেই connect করে। Windows Settings-এ সবসময় “Connected” লেখা না থাকলেও paired থাকা যথেষ্ট; actual socket খুললে connection হবে।

## ৫. Phone app configure করুন

1. BlueDrop AI খুলুন।
2. HTTPS URL: `https://ai.yourdomain.com` দিন; শেষে `/v1/jobs` যোগ করবেন না।
3. Bridge token: `.env`-এর BRIDGE_TOKEN paste করুন।
4. Load paired devices চাপুন; Nearby devices permission Allow করুন।
5. তালিকায় আপনার **PC** বেছে নিন।
6. Save & test backend চাপুন। “Connected • openai/gpt-oss-120b” আসা উচিত।
7. Start bridge চাপুন। “Ready • waiting for your PC” মানে ready, freeze নয়।

Settings বদলাতে Stop bridge দিন। Token একবার save হলে field ফাঁকা রাখলে পুরোনো token থাকবে। URL/token বদলালে আবার test করুন।

## ৬. Windows terminal chat

যেকোনো folder-এ শুধু নতুন `receiver.py` রাখুন। পুরো project রাখার প্রয়োজন নেই। উদাহরণ:

```powershell
py -3 "C:\BlueDropAI\receiver\receiver.py" --setup
```

List থেকে আপনার Android phone-এর number দিন। আপনার আগের উদাহরণে ফোন ছিল “Raf's A15”, কিন্তু এবার actual list দেখে select করবেন। Save folder prompt-এ দিন:

```text
C:\Users\sohel\Downloads\BlueDropAI
```

Enter দিয়ে setup শেষ করুন। তারপর:

```powershell
py -3 "C:\BlueDropAI\receiver\receiver.py" --chat
```

Terminal:

```text
You: Explain C++ pointers in Bangla.
  [forwarding]
  [running]
AI: ...reply...

You: Give me an example using an array.
AI: ...context-aware reply...
```

প্রতি মেসেজ শেষে Enter চাপবেন। `AI:` উত্তর আসার পর পরের প্রশ্ন লিখবেন। Token-by-token streaming নয়; complete reply দেখাবে। Terminal control codes filter করা হয়; returned code execute হয় না।

- `/new` → আগের conversation context বাদ দিয়ে নতুন chat। Saved TXT files থাকে।
- `/quit` → client বন্ধ।
- `Ctrl+C` → client বন্ধ; কোনো request pending থাকলে পরেরবার একই request resume হবে।
- Empty Enter → কিছু পাঠাবে না।

প্রতি reply `answer-<request-uuid>.txt` নামে UTF-8 হিসেবে save হবে। একই request replay হলে একই file রাখবে। আগে save করা file আপনি edit করলে overwrite করবে না; file অন্য জায়গায় সরিয়ে client আবার চালান। Local writable NTFS folder ব্যবহার করুন।

Default config/history:

```text
%LOCALAPPDATA%\BlueDropAI\receiver-config.json
%LOCALAPPDATA%\BlueDropAI\receiver-config-chat.json
```

`receiver.py` অন্য folder-এ সরালেও settings থাকবে। History file-এ recent chat ও pending prompt থাকে; নিজের Windows account-এ রাখুন। Custom আলাদা profile চাইলে `--config "C:\MyAI\config.json"` setup এবং chat—দুই command-এই ব্যবহার করুন। এক config দিয়ে একসঙ্গে এক client চলবে।

## ৭. Folder বদলানো / TXT prompt

পরে save folder বদলাতে Ctrl+C, আবার `--setup`, নতুন folder দিন। শুধু একবারের override:

```powershell
py -3 "C:\BlueDropAI\receiver\receiver.py" --chat --output "C:\Users\sohel\Downloads"
```

TXT prompt পাঠাতে:

```powershell
py -3 "C:\BlueDropAI\receiver\receiver.py" --send "C:\BlueDropAI\samples\question.txt"
```

TXT UTF-8 হতে হবে। এটি ওই file-এর content দিয়ে fresh context শুরু করে। Conversation-এর জন্য terminal chat-ই প্রধান mode।

## ৮. প্রথম real-device acceptance test

1. Phone mobile data চালু করুন এবং phone app-এর backend test সফল করুন।
2. PC-র Wi-Fi/Ethernet internet বন্ধ করুন, Bluetooth চালু রাখুন।
3. Terminal-এ `Reply exactly: Bluetooth AI works` পাঠান। Reply ও TXT save দেখুন।
4. বলুন `Remember the number 17`। এরপর `What number did I ask you to remember?` দিন।
5. একটি prompt পাঠিয়ে reply আসার আগে PC Bluetooth সাময়িক off/on করুন। একই request resume এবং একটি TXT file পাওয়া যাচাই করুন।
6. Phone screen lock করে আরেকটি প্রশ্ন পাঠিয়ে পরীক্ষা করুন। Manufacturer battery restriction থাকলে BlueDrop AI-এর battery setting Unrestricted করুন।
7. Ctrl+C দিয়ে client বন্ধ ও আবার চালু করে conversation context টিকে আছে কিনা দেখুন।

## সমস্যা হলে

| লক্ষণ | করণীয় |
|---|---|
| Bluetooth connect timeout | Bridge Start আছে কিনা, দুই পাশে সঠিক device selection, pairing, adapter driver দেখুন; প্রয়োজনে re-pair |
| Ready / waiting | স্বাভাবিক idle state; PC থেকে message পাঠান |
| Backend token rejected | Stop → BRIDGE_TOKEN মিলিয়ে Save & test → Start |
| Backend 404 | Base URL ঠিক করুন; API path app নিজেই যোগ করে |
| Phone cannot reach backend | Mobile data, DNS, HTTPS certificate, VPS firewall ও broker process দেখুন |
| Groq HTTP 401 | `.env`-এর Groq key ঠিক করুন; container recreate; নতুন message পাঠান |
| Groq HTTP 429 | Groq quota/rate limit দেখুন; পরে নতুন message দিন |
| Broker restarted during generation | আগের call-এর outcome অনিশ্চিত; নিজে নতুন message পাঠান। Auto regenerate করে double charge করা হয় না |
| Screen lock করলে বন্ধ | Battery restriction পরীক্ষা করুন; app manually start করুন; Android force-stop/reboot-এর পরে auto-start নেই |
| SDK location not found | ANDROID_HOME বা local.properties ঠিক করুন |
| Autolinking fails | `mobile` থেকে `npx react-native config` চালান; config-এ পুরোনো `project.ios: null` যোগ করবেন না |
| currentActivity compile error | এই project-এ `ctx.currentActivity` আছে; পুরোনো Kotlin file mix করবেন না |
| Cannot save / permissions | Writable local NTFS folder দিন; disk space দেখুন; output-এর existing edited file সরান |

প্রথম setup, backend deployment, key/token ও Bluetooth pairing ছাড়া zero-configuration connection সম্ভব নয়। এগুলো একবার হয়ে গেলে phone bridge + Python terminal চালিয়েই chat করতে পারবেন।
