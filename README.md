# Abe — Quadra CodeLinc

Abe is a voice-first AI dental-benefits assistant for the CodeLinc hackathon. A phone call streams through Twilio, the TypeScript gateway, and Amazon Nova 2 Sonic. Nova converses and chooses allowlisted tools; the private Python/FastAPI backend owns exact member facts, structured plan rules, plan-document evidence, provider facts, and deterministic insurance arithmetic.

Everything committed under `data/demo/` is synthetic project-authored data—not Lincoln customer data, PHI, a real plan, or real dentists. `DEMO001` retains its deterministic **$800 remaining annual dental maximum**.

A real Twilio → Nova phone call has not been certified for this milestone; automated transport and backend tests pass, but the owner must run the manual call test with configured credentials.

## Architecture

```text
Phone → Twilio Media Streams → TypeScript voice gateway ↔ Nova 2 Sonic
                                      ↓ allowlisted tools
                              Python/FastAPI Abe core
                    ┌─────────────┬────────────┬─────────────┐
                    ↓             ↓            ↓             ↓
              Member repo   Plan rules   Plan retrieval  Provider repo
                    └─────────────┴──────┬─────┴─────────────┘
                                        ↓
                              Decimal benefit calculator
```

Nova understands the caller and explains results. It is not the source of member/plan/provider facts and does not do insurance arithmetic. Plan-document retrieval provides supporting language and source metadata; structured plan rules drive calculations. Provider search filters options and does not perform Milestone 3 optimization.

## Install

Use Node.js 22+, Python 3.12+, and npm:

```bash
python3.12 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements-dev.txt
npm --prefix voice-gateway ci
```

Copy `.env.example` to an ignored `.env`, generate an `ABE_INTERNAL_TOKEN` of at least 32 printable non-whitespace ASCII characters, and keep all assignments shell-compatible. Never commit or paste secrets.

Local no-AWS data modes are explicit:

```dotenv
MEMBER_REPOSITORY=synthetic
PLAN_RULES_SOURCE=local
RAG_PROVIDER=local
PROVIDER_REPOSITORY=synthetic
```

For live voice, also set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `PUBLIC_BASE_URL`, `AWS_REGION`, `NOVA_MODEL_ID`, and `NOVA_VOICE_ID`. The AWS SDK uses its standard credential chain; `AWS_PROFILE` is optional. The code never stores AWS credentials or provisions infrastructure.

Production adapter selections are fail-closed:

```dotenv
MEMBER_REPOSITORY=dynamodb
DYNAMODB_MEMBER_TABLE=existing-table-name
RAG_PROVIDER=bedrock
BEDROCK_KNOWLEDGE_BASE_ID=existing-knowledge-base-id
```

Selecting an AWS adapter without its required identifier fails startup; it never silently substitutes synthetic data. See [Milestone 2 data and AWS adapters](docs/milestone-2.md) for schemas, least-privilege guidance, metadata, and manual setup.

## Run locally

Use three terminals at the repository root:

```bash
./scripts/run-backend.sh
```

```bash
ngrok http 3000
```

Set `PUBLIC_BASE_URL` to ngrok's HTTPS origin, then:

```bash
npm --prefix voice-gateway run dev
```

Keep the backend on loopback and tunnel only gateway port 3000. Configure the Twilio number's incoming Voice webhook as `POST https://YOUR-TUNNEL-HOST/twilio/incoming`. The returned TwiML opens `wss://YOUR-TUNNEL-HOST/twilio/media`; request/WebSocket signature validation remains mandatory.

Calls are limited to seven minutes and four concurrent calls per gateway process. Backend sessions are process-local, bounded to 100, expire after 15 idle minutes or one hour total, and disappear on restart. Run one backend worker.

## Conversation and tools

Nova receives a natural-conversation system prompt and retains its streaming call context. Greetings, thanks, rephrasing, follow-ups, and corrections do not require canned phrases. A validated member is reused for related turns. Invalid ID attempts no longer erase valid session context.

Spoken IDs go verbatim to `resolve_member_id`; deterministic parsing handles case, separators, individually spoken letters/digits, `oh`, digit words, `double`, and `triple`, then validates candidates against the selected member repository. Unknown input requests repetition and ambiguous repository matches require confirmation.

The allowlisted tools are:

- `resolve_member_id`
- `get_member`
- `retrieve_plan_context`
- `search_providers`
- `calculate_benefit`

The calculator requires a real synthetic-fixture or caller-supplied provider charge, a plan-required allowed amount, network status, and treatment date. It uses `Decimal` with cent rounding, applies the structured deductible/coverage rule, caps plan payment at the stored remaining annual maximum, and returns provenance, steps, assumptions, and warnings. It never calls an LLM.

During barge-in, the gateway sends Twilio `clear`, discards late audio from the interrupted Nova completion, continues streaming caller audio, and keeps the Nova/backend session alive. Hangup and failures still release the call's resources.

## Checks and smoke tests

No AWS or Twilio credentials are needed:

```bash
./scripts/check.sh
./scripts/smoke-benefits.sh
```

`check.sh` runs backend tests, Ruff lint/format, mypy, the backend-only benefit smoke test, gateway tests, Biome, TypeScript checks, and the build. The smoke path is `DEMO001 → synthetic member → demo plan → synthetic provider → Decimal calculator`.

With the backend running and `ABE_INTERNAL_TOKEN` configured, `./scripts/smoke-tool.sh` additionally exercises the authenticated HTTP session/tool boundary and confirms the stored $800 balance.

## Safety and limits

Abe gives benefit information and estimates, not diagnoses, clinical urgency, or advice to delay care. Estimates are not coverage guarantees. The current milestone does not optimize providers, dates, payment methods, annual resets, FSA use, travel, or treatment timing; it does not include a React UI, handoff, deployment, or production Lincoln integration.

Application logs contain safe lifecycle/tool status events, not secrets, transcripts, or member facts. External requests, model tool arguments, and backend results remain bounded and validated. See [voice protocol notes](docs/voice-protocol.md) for the wire protocol and security boundary.
