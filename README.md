# Abe — Quadra CodeLinc

Milestone 3 adds response-scoped barge-in recovery, existing AWS demo adapters and a
deterministic scenario optimizer. See [setup, verified AWS limitations and phone acceptance](docs/milestone-3.md).

Abe is an AI dental-benefits assistant built for the Quadra CodeLinc hackathon. Milestone 2 connects phone audio to Amazon Nova 2 Sonic, bounded conversation state, member lookup, plan retrieval, synthetic provider search, and a deterministic Python benefit calculator. The synthetic member `DEMO001` has **$800 remaining** in their demo annual dental maximum; that value comes directly from stored data.

Milestone 2 includes automated protocol, API, repository, calculator and cross-language contract tests. **Milestone 2 real-phone acceptance remains a user-run test**; automated tests do not prove live conversation or interruption quality.

The new [companion app](docs/companion-app.md) adds a responsive conversation inbox, persistent call transcripts, verified recap cards, and text chat using the same Abe tools.

## One-command judging demo (no credentials)

On **macOS or Linux**, install **Node.js 22+ with npm** and **Python 3.12+ with venv**.
From the repository root, paste this into the judging form's **Build and start command**:

```bash
bash scripts/run-demo.sh
```

The command installs dependencies, builds both JavaScript components, starts all
three services, and checks each service before printing **ABE DEMO READY**.
Internet access is needed for dependency installation. Open **http://127.0.0.1:3000**
for the guided demo and **http://127.0.0.1:5173** for the React companion.
Press **Ctrl+C** to stop all three. Ports 8000, 3000 and 5173 must be available.
On Windows, use WSL with these prerequisites installed inside WSL. For an existing
live setup, choose unused ports without stopping it:

```bash
ABE_DEMO_BACKEND_PORT=18000 ABE_DEMO_PORT=13000 ABE_DEMO_FRONTEND_PORT=15173 bash scripts/run-demo.sh
```

No `.env`, AWS account, Twilio account, tunnel, API key or paid service is needed.
The launcher ignores local `.env` and inherited service configuration, generates
an ephemeral internal authentication token, and uses synthetic repositories.
The guided scenario runs real normalization, member lookup, local plan retrieval,
provider search, deterministic calculation and optimization through the gateway's
existing backend client. It uses a fixed synthetic treatment date, November 1, 2026.
The companion provides its existing sample conversations, transcripts and summaries.
**Free-form Nova chat and real telephone/audio calls are not simulated:** they
require the live setup below. The demo gateway uses a separate loopback-only entry
point; production webhook authentication and audio handling are unchanged.
Do not expose the offline demo through a public tunnel.

## Architecture

```text
Phone → Twilio → Node/TypeScript gateway ↔ Nova 2 Sonic
                              ↓ tools + final transcripts
React companion → Python/FastAPI Abe core ↔ Nova text
                              ↓
                  Shared benefits tools + conversation storage
```

Nova understands and explains; the backend owns instructions, sessions, tools, and member facts. The Python calculator performs insurance arithmetic; scenario optimization is deterministic Python. Abe does not diagnose or determine whether delaying treatment is safe.

```text
voice-gateway/src/     HTTP/WebSocket lifecycle, audio codec, telephony and Nova adapters
voice-gateway/tests/   Protocol, codec, authentication and failure-path tests
backend/app/          Agent instructions, sessions, typed tools and member repository
backend/tests/        API, validation, stored-balance and session-limit tests
frontend/             React companion: inbox, call details, transcript and text chat
data/demo/            Synthetic JSON member fixture
docs/                 Voice protocol notes and official references
scripts/              Local startup, checks and tool smoke test
```

Local synthetic data and local plan retrieval work without AWS data services. Optional read-only DynamoDB and Bedrock Knowledge Base adapters are included. No infrastructure is provisioned. Milestone 3 adds bounded optimization and FSA funding estimates; handoff and deployment remain out of scope.

See [Milestone 2 behavior, demo scenarios, AWS setup and limitations](docs/milestone-2.md).

## Install

Use Node.js **22+**, Python **3.12+**, and npm. For a live call, also install the AWS CLI and an authenticated ngrok CLI, and select a Twilio number with Voice capability.

From the directory containing your checkout:

```bash
cd QuadraCodeLinc
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix voice-gateway ci
npm --prefix frontend ci
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
| `NOVA_VOICE_ID` | Example uses `matthew` |
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

Spoken IDs first go to the backend `resolve_member_id` tool. Say “demo zero zero one,” “D E M O zero zero one,” or “demo double zero one.” Deterministic parsing checks candidates against the member repository before returning `DEMO001` to `get_member`. Unrecognized wording requests repetition; multiple valid interpretations require confirmation. No fuzzy correction or arbitrary filler-word removal is performed. Restart both services after updating this tool contract.

This is a **synthetic-data demo without caller identity verification**. Do not connect it to real member records. Backend sessions are process-local, capped at 100, expire after 15 minutes idle or one hour total, and disappear on restart. Run one backend worker.

## Checks

No AWS or Twilio credentials are needed for automated tests:

```bash
./scripts/check.sh
```

This runs Python tests, Ruff lint/format checks, mypy, gateway tests, Biome checks, TypeScript checks, the gateway build, and the offline benefits smoke. Every stage has a time limit. Install both language environments first: gateway contract tests exercise real Python HTTP serialization. Run `./scripts/smoke-benefits.sh` independently for an offline benefits check; it starts no server and needs no AWS credentials. With the backend already running, verify the real local HTTP tool path:

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
