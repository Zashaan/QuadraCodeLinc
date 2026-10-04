# Abe companion app

The app brings call history, benefit details, transcripts, and text chat into one responsive workspace. The visual direction follows the supplied Figma screenshot: burgundy, soft neutral backgrounds, rounded conversation cards, and a calm conversational style. The linked Figma file was not accessible; the screenshot was the visual reference. The existing partial conversation models, repositories, seed data, and recap builder were reused.

## Try it locally

1. Install and build the frontend from the repository root:

   ```bash
   npm --prefix frontend ci
   python3 scripts/with-timeout.py 60 npm --prefix frontend run build
   ```

2. Add the companion settings from `.env.example` to your ignored root `.env`. Generate a **separate** app key without printing it (this preserves an existing nonempty value):

   ```bash
   backend/.venv/bin/python - <<'PY'
   from pathlib import Path
   import secrets
   path = Path('.env')
   lines = path.read_text().splitlines()
   found = False
   for index, line in enumerate(lines):
       if line.startswith('ABE_MEMBER_TOKEN='):
           found = True
           if not line.split('=', 1)[1].strip():
               lines[index] = 'ABE_MEMBER_TOKEN=' + secrets.token_urlsafe(32)
   if not found:
       lines.append('ABE_MEMBER_TOKEN=' + secrets.token_urlsafe(32))
   path.write_text('\n'.join(lines) + '\n')
   path.chmod(0o600)
   PY
   ```

   `CONVERSATION_REPOSITORY=sqlite` is the environment default. Relative database paths resolve from the repository root. Optionally set `ABE_DEMO_CALL_NUMBER` to your existing Twilio number, with country code and leading `+`.

3. Start or restart the backend yourself with `./scripts/run-backend.sh`, then open **http://127.0.0.1:8000**. FastAPI serves `frontend/dist` alongside the protected app API. Build the frontend before starting the backend. Do not expose this port through the voice tunnel.

4. Without connecting, you can explore clearly labeled sample conversations. Choose **Explore demo**, copy `ABE_MEMBER_TOKEN` from your local `.env` into the app's access-key field, and connect. The browser retains it only for that tab. Never enter the internal service key. This is a single synthetic-member demo gate, not production account authentication.

5. For text replies, use the same AWS credential chain as voice. The identity needs `bedrock:InvokeModel` access to `NOVA_TEXT_MODEL_ID` and its underlying model resources when using an inference profile. The default is `us.amazon.nova-2-lite-v1:0`. No AWS services or permissions are provisioned automatically. A missing/expired credential or unavailable model produces an honest saved failure message, never a fabricated response.

Restart the gateway after rebuilding it (`npm --prefix voice-gateway run build`) so new calls send transcripts. Calls made before this change cannot be reconstructed unless transcripts were already saved elsewhere. Keep the existing gateway-only tunnel and Twilio configuration.

The implementation/check run did not start persistent servers, place phone calls, or invoke paid models. The commands above are for you to start the app when ready.

## What works

- Search and filter calls and messages. Open a call's overview or speaker-labeled transcript, search within it, and export it as a text file.
- Overview cards show the procedure, verified provider name, estimated member/plan amounts, stored annual-maximum/FSA balances, relevant notes, and available plan-document sources. Amounts come from existing tools; the UI only formats them. Unknown information is omitted. Estimates do not spend benefits.
- Use **Ask Abe about this call** to carry its recap into text chat. The same allowlisted Python benefit tools serve voice and text. The model cannot run arbitrary code or switch the text session away from DEMO001.
- Incoming completed speech turns appear in the app on its next refresh (every 10 seconds while visible). Only Nova's `FINAL` user/assistant text is saved; speculative speech and interruption markers are excluded. This follows [Nova's transcript contract](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-chat-history.html).
- SQLite stores conversations at `.local/abe-conversations.sqlite3`, with owner-only file permissions. Calls survive restarts. Interrupted saves are labeled partial; sessions left open at a backend restart are also marked partial. No audio recordings are stored.
- The phone button uses the configured number. When no number is configured it says so. It does not pretend to initiate a connected call.

## API and limits

App routes require `Authorization: Bearer <ABE_MEMBER_TOKEN>`, scoped to DEMO001:

- `GET /api/conversations`
- `GET /api/conversations/{conversation_id}`
- `POST /api/chat/messages` with `text` and optional `context_conversation_id`

Internal transcript ingestion uses the existing internal token:

- `POST /sessions/{session_id}/transcripts` with up to 20 `{event_id, role, text}` events and optional `incomplete`. SQLite and in-memory modes deduplicate event IDs. Session deletion finalizes the recap while retaining the conversation.

Use **one backend worker**: session state and the chat concurrency guard are process-local. Text sends are serialized, limited to 2,000 input characters, 4 model rounds, and bounded service timeouts; model output is validated and limited to 4,000 characters. Only the latest 24 chat messages are sent as context. Each conversation retains its latest 500 messages; the SQLite inbox lists the latest 100 conversations plus pinned ordering. The database is local demo storage, not an indefinite archival service. Transcript timestamps indicate receipt time, not audio offsets.

All callers share the synthetic demo workspace. Before real members or a public launch, account authentication and per-member call ownership are separate work. Source fixtures are illustrative and labeled; sample recaps must not be treated as actual recent calls. The existing optional DynamoDB conversation adapter is preserved as experimental and is not the validated persistence path for this slice. No tables are created.

## Validation

`./scripts/check.sh` runs the backend, gateway, and frontend test suites plus formatting, lint, type checks, builds, and the existing benefit smoke scenarios. Every stage has a timeout and tests exit automatically.

Automated coverage includes separate app/internal authentication, ownership filtering, durable call completion, duplicate events, partial/restarted calls, corrected-member recap clearing, text tool calls and refusals, bounded model loops, saved failure replies, the installed Bedrock Converse contract, transcript event filtering, nonblocking audio during transcript saves, inbox/transcript search, preview connection requirements, and text send/retry behavior.

Desktop (1440px) and mobile (390px) browser checks exercised call selection, transcript search/export, chat, and the connection dialog without browser errors or horizontal overflow. A real phone-to-app call and a real Nova text exchange still require user-run acceptance with the configured services.
