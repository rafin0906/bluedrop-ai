# Architecture and protocol v2

## Bluetooth

Android is the **server**, Windows is the **client**. Service UUID:
`c91c315b-8c0b-487f-a640-c073e9415d55`.
Secure Classic RFCOMM, paired-device authentication/encryption. Android accepts only the bonded PC selected in app settings. The Windows native Winsock adapter asks SDP to resolve this UUID. No fixed COM port or receiver-side Bluetooth library is required.

Each frame: 4-byte unsigned big-endian length followed by a UTF-8 JSON object. Maximum encoded frame 1,048,576 bytes. Socket sessions carry one chat request; the server stays listening between requests. File prompts are decoded by Python before transmission; text and chat use the same protocol.

PC sends:

```json
{"type":"chat","version":2,"id":"7d091fdd-a202-44fa-94a3-b43e40aa3195","messages":[{"role":"user","content":"Hello"}]}
```

Phone sends zero or more progress frames, then one result or error:

```json
{"type":"status","id":"7d091fdd-a202-44fa-94a3-b43e40aa3195","status":"running"}
```

```json
{"type":"result","id":"7d091fdd-a202-44fa-94a3-b43e40aa3195","answer":"Hello!","finish_reason":"stop"}
```

```json
{"type":"error","id":"7d091fdd-a202-44fa-94a3-b43e40aa3195","message":"Backend busy; PC will retry.","retryable":true}
```

PC commits TXT and chat state before sending `{"type":"ack","id":"..."}`. Then closes the connection. Terminal output is sanitized; saved TXT preserves the answer. No `eval`, shell execution, remote executable installation, or model tool execution exists.

## HTTPS API

All `/v1/*` endpoints require `Authorization: Bearer <BRIDGE_TOKEN>`.

| Method / path | Meaning |
|---|---|
| GET `/healthz` | Public process health, no Groq call |
| GET `/v1/config` | Authenticated model name, context limit, protocol version |
| POST `/v1/jobs` | Body `{id, messages}`; persist new job or return existing one |
| GET `/v1/jobs/{id}` | Fetch queued/running/completed/failed job |
| GET `/docs` | FastAPI generated interactive endpoint reference |

Successful job JSON keys: `id`, `status`, `answer`, `error`, `finish_reason`. Answer/error may be null until ready. IDs are UUIDs; messages allow only `user` and `assistant`, with last role `user`. Maximum 40 messages, 64 KiB combined UTF-8 content, 512 KiB encoded HTTP request, 200 KiB UTF-8 answer, 8,192 maximum Groq completion tokens. Output may be shorter if reasoning consumes the budget; `finish_reason=length` is shown on the PC.

HTTP 401 authentication; 404 missing route/job; 409 ID/content conflict; 413 size; 422 malformed data; 429 queue or submission limit. Phone retries 429/5xx and network failures via the PC retry loop. Groq failures become terminal failed jobs; a new user submission gives a new ID.

## Durability and retries

- PC persists pending ID and full request before Bluetooth transmission. Recent context and pending data live beside its config in LOCALAPPDATA by default.
- Every reconnect sends the same ID/content. SQLite deduplicates before queuing; completed replies can be fetched again without a second provider call.
- Backend stores digest/status/results persistently. Prompts are removed from job rows after completion/failure; results remain for retry. SQLite old pages/backups can still retain historical data. This is not secure erasure.
- A backend crash while a provider call is running marks that job failed on restart. Its outcome is ambiguous, so it is not auto-reissued. This is not an exactly-once guarantee across the external Groq service.
- One replica, one Uvicorn worker, one SQLite database. Do not run a second broker on the same database: startup recovery would misidentify live work as interrupted.
- At most 20 queued/running jobs and 20 new requests/minute. Generation is serial. Results have no automatic expiry; monitor persistent disk, back up the DB, and prune old completed jobs only when pending clients no longer need them.
- PC history keeps at most 19 complete turns locally; requests trim oldest complete turns to fit 64 KiB. `/new` clears context only. Replies stay on disk.
- If reply storage fails, fix local disk/permissions and resume the pending request; backend deduplication prevents repeated generation.

## Operational boundaries

The phone must have working internet to reach your backend. PC Bluetooth alone does not give the PC general internet access. Foreground notification and an active-request wake lock keep the bridge available within Android's lifecycle constraints; force-stop, reboot, Bluetooth removal, and vendor battery management still require attention. Background startup after reboot is deliberately not implemented.

The phone stores only configuration and an Android Keystore-encrypted bridge token persistently; prompts/replies are forwarded in memory. HTTPS certificate validation is enabled and redirects are not followed (prevents sending the token to a different host). Use the final canonical HTTPS URL.

The backend is a single-user personal broker. A shared bearer token is not a multi-tenant login system. Groq key stays on backend; model endpoint is fixed to Groq's HTTPS API. The server receives conversation text and Groq processes it. The application does not browse, execute code, or forward arbitrary filesystem paths.
