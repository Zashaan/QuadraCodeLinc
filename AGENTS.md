# Abe engineering rules

Abe is a voice-first AI dental-benefits assistant. Identify it as AI, speak calmly and concisely, and use synthetic demo members only.

## Architecture and ownership

Phone → Twilio → Node/TypeScript voice gateway → Amazon Nova 2 Sonic → Abe Python/FastAPI agent core → explicit tools → spoken response.

Future text: React/Vite → FastAPI → Nova text model → the same Abe tools. Do not build the frontend as part of the first voice milestone.

- Nova understands language, extracts intent/preferences, conducts conversation, chooses tools, and explains their results.
- RAG will retrieve trusted plan information; the member repository retrieves member facts; provider search will retrieve provider options.
- Python calculators will do deterministic insurance arithmetic; Python optimizers will build and rank valid scenarios.
- The gateway owns telephony, audio formats, streaming, and lifecycle only. Keep telephony and Nova behind replaceable interfaces.
- The backend owns sessions, agent instructions, tool definitions, and member lookup.
- Models must never invent coverage, member facts, or provider facts, perform insurance arithmetic, or independently determine medical safety. Remaining balances must come from the tool/data layer.
- Abe does not diagnose or decide whether delaying treatment is safe. Future timing recommendations require dentist-provided safe timing or conditional wording: “If your dentist says either timing is clinically appropriate…”

## Security and cost

- Never commit or log secrets, tokens, credentials, `.env`, full headers, or unnecessary caller data. Use environment variables locally and Secrets Manager for future deployments where appropriate.
- Validate external events, model/tool arguments, and structured results. Use explicit allowlisted tools; never give the model arbitrary shell, filesystem, database, or AWS access.
- Authenticate Twilio webhooks/WebSocket handshakes and internal backend requests. Keep the backend private.
- Use least privilege. Do not provision AWS resources without explicit authorization. No EKS, GPUs, NAT gateways, large EC2 instances, provisioned databases, or unnecessary deployment infrastructure.
- Start locally through a secure tunnel. Do not add DynamoDB, RAG, provider search, calculators, optimizers, handoff, React, or deployment infrastructure during the first milestone.

## Development

- Inspect relevant existing code once, preserve useful work, and ship vertical slices. Prefer simple reliable modules over abstractions or broad refactors.
- Use strict types, schema validation, timeouts, bounded buffers, structured lifecycle logs, and graceful cleanup.
- Verify external SDK contracts with installed definitions or official documentation. Do not invent API methods or audio encodings.
- Test deterministic logic, HTTP/WebSocket behavior, audio conversion, and meaningful failure paths. Run tests, lint, formatting, and type checks before delivery.
- Keep credit use and explanations concise. Resolve routine issues autonomously; only ask for genuinely necessary external actions. Never ask for secrets in chat.

## First milestone contracts

- Backend: `GET /health`; authenticated `POST /sessions` with `{call_id}` returns `{session_id, system_prompt, tools}`; `DELETE /sessions/{session_id}`; `POST /sessions/{session_id}/tools` with `{tool_name, tool_call_id, arguments}` returns `{tool_name, tool_call_id, result}`.
- Tools have `{name, description, input_schema}`. `get_member` takes `{member_id}`; result is `{status:"success", member:{...}}` or `{status:"not_found", member_id}`. DEMO001 has annual maximum 2000, used 1200, remaining 800, deductible total/used 50, remaining 0, FSA balance 350 (USD).
- Internal requests use `Authorization: Bearer <ABE_INTERNAL_TOKEN>`. Only the gateway is exposed through the tunnel.
- Spoken IDs go verbatim to `resolve_member_id` before `get_member`. Python normalizes case, separators, digit words, and double/triple digits, then checks repository matches. Only one match resolves directly; none requests repetition, multiple require caller confirmation. Never strip arbitrary words or let Nova invent canonical IDs.
- Nova adapter owns `src/nova/`; the gateway owns HTTP, telephony, audio codec and backend client. Do not mix benefit rules into the gateway.
