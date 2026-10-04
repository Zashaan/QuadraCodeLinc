# Abe — Quadra CodeLinc

Abe is an AI dental-benefits assistant built for the Quadra CodeLinc hackathon. This first slice connects phone audio to Amazon Nova 2 Sonic and one deterministic Python tool: `get_member`. The synthetic member `DEMO001` has **$800 remaining** in their demo annual dental maximum; that value comes directly from stored data.

The integration is implemented and covered by automated tests. A real Twilio → Bedrock phone call has **not yet been tested**; it requires local credentials, model access, a tunnel, and a configured Twilio number.

## Architecture

```text
Phone → Twilio → Node/TypeScript voice gateway ↔ Nova 2 Sonic
                              ↓ tool requests
                      Python/FastAPI Abe core
                              ↓
                      Synthetic member repository
```

Nova understands and explains; the backend owns instructions, sessions, tools, and member facts. Future calculators and optimizers will perform insurance arithmetic. Abe does not diagnose or determine whether delaying treatment is safe.

```text
voice-gateway/src/     HTTP/WebSocket lifecycle, audio codec, telephony and Nova adapters
voice-gateway/tests/   Protocol, codec, authentication and failure-path tests
backend/app/          Agent instructions, sessions, typed tools and member repository
backend/tests/        API, validation, stored-balance and session-limit tests
data/demo/            Synthetic JSON member fixture
docs/                 Voice protocol notes and official references
scripts/              Local startup, checks and tool smoke test
```

No frontend, RAG, DynamoDB, provider search, benefit calculator, optimization, handoff, or AWS infrastructure is included.

## Install

Use Node.js **22+**, Python **3.12+**, and npm. For a live call, also install the AWS CLI and an authenticated ngrok CLI, and select a Twilio number with Voice capability.

From the directory containing your checkout:

```bash
cd QuadraCodeLinc
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix voice-gateway ci
```

## Local configuration

For first-time setup, copy the example and generate the shared backend token directly into the ignored file. This command does not print the token:

```bash
cp .env.example .env
chmod 600 .env
backend/.venv/bin/python - <<'PY'
from pathlib import Path
import secrets

path = Path('.env')
text = path.read_text()
text = text.replace('ABE_INTERNAL_TOKEN=\n', f'ABE_INTERNAL_TOKEN={secrets.token_urlsafe(32)}\n')
path.write_text(text)
PY
```

Edit `.env` locally. Never paste credentials into chat or commit `.env`. Keep its assignments shell-compatible because the backend startup script loads it as a shell file.

| Variable | Purpose |
| --- | --- |
| `TWILIO_ACCOUNT_SID` | Account that owns the Voice number |
| `TWILIO_AUTH_TOKEN` | Auth token for validating Twilio signatures |
| `PUBLIC_BASE_URL` | Current public HTTPS tunnel origin, without a path or query |
| `ABE_INTERNAL_TOKEN` | Shared generated secret; at least 32 printable ASCII characters |
| `ABE_BACKEND_URL` | Private backend origin; example uses local port 8000 |
| `PORT` | Local gateway port; example uses 3000 |
| `AWS_REGION` | Bedrock region; example uses `us-east-1` |
| `NOVA_MODEL_ID` | Example uses `amazon.nova-2-sonic-v1:0` |
| `NOVA_VOICE_ID` | Example uses `tiffany` |
| `AWS_PROFILE` | Optional existing AWS profile for the SDK credential chain |

The AWS SDK uses its standard credential chain. For an existing SSO profile, authenticate locally and put the matching profile name in `AWS_PROFILE`:

```bash
aws sso login --profile YOUR_PROFILE
```

Your AWS identity needs `bedrock:InvokeModel` permission for `arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-2-sonic-v1:0` and access to that model in the selected region. The code does not provision resources. See the [Bedrock bidirectional API permissions](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_InvokeModelWithBidirectionalStream.html) and [Nova 2 Sonic setup](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-getting-started.html).

## Run locally

Use three terminals at the repository root:

```bash
# Terminal 1: private backend, bound to 127.0.0.1:8000
./scripts/run-backend.sh
```

```bash
# Terminal 2: public tunnel to the gateway only
ngrok http 3000
```

Set `PUBLIC_BASE_URL` in `.env` to the HTTPS origin shown by ngrok, then start the gateway:

```bash
# Terminal 3: gateway, bound to 127.0.0.1:3000
npm --prefix voice-gateway run dev
```

Both startup commands read the root `.env`. Restart the gateway after changing its configuration. Keep the backend private; tunnel only port 3000. To run compiled gateway code instead, use `npm --prefix voice-gateway run build` followed by `npm --prefix voice-gateway start`.

## Twilio configuration and demo

In Twilio Console, open **Phone Numbers → Manage → Active numbers**, select the Voice number, and configure **A call comes in** to use **Webhook**:

| Setting | Value |
| --- | --- |
| Incoming webhook | `https://YOUR-TUNNEL-HOST/twilio/incoming` |
| HTTP method | `POST` |
| Media WebSocket | `wss://YOUR-TUNNEL-HOST/twilio/media` |

Save the number configuration. The webhook returns `<Connect><Stream>` pointing to the WebSocket; no separate WebSocket setting is needed. Use the same hostname as `PUBLIC_BASE_URL`, preserve TLS validation, and do not disable signature checks. A normal unsigned browser or curl request to these endpoints is intentionally rejected.

Call the number and ask “How much dental benefit do I have left?” Then give demo member ID `DEMO001`. Abe should use `get_member` and explain the stored $800 remaining amount. Also try an unknown ID, interrupt a spoken reply, and hang up to verify cleanup. Calls are limited to **seven minutes**, with **four concurrent calls** per gateway process.

This is a **synthetic-data demo without caller identity verification**. Do not connect it to real member records. Backend sessions are process-local, capped at 100, expire after 15 minutes idle or one hour total, and disappear on restart. Run one backend worker.

## Checks

No AWS or Twilio credentials are needed for automated tests:

```bash
./scripts/check.sh
```

This runs Python tests, Ruff lint/format checks, mypy, gateway tests, Biome checks, TypeScript checks, and the gateway build. With the backend running, verify the real local HTTP tool path:

```bash
./scripts/smoke-tool.sh
```

The smoke test reads local configuration, creates a session, invokes the synthetic lookup, checks the stored $800, and deletes the session.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Gateway reports invalid configuration | Fill the named variables in `.env`; errors intentionally omit values. |
| Twilio HTTP/WebSocket 403 | Verify the account/token pair and exact current public tunnel origin; restart after URL changes. |
| Backend 401 or lookup unavailable | Both processes must use the same `ABE_INTERNAL_TOKEN`; confirm the backend is running. |
| Nova fails during call setup | Refresh AWS login, check `AWS_PROFILE`, region, model permission/access, and outbound HTTP/2 connectivity. |
| Call ends early | Inspect structured event reasons for startup timeout, malformed events, upstream failure, buffer limit, or the seven-minute cap. |
| Audio is distorted | Preserve raw 8 kHz μ-law ↔ PCM16 mono conversion; do not add WAV headers or resampling. |

Health checks are `http://127.0.0.1:8000/health` and `http://127.0.0.1:3000/health`. Application logs contain lifecycle events, not transcripts or member facts. See [voice protocol notes](docs/voice-protocol.md) for wire formats, boundaries, and official sources.
