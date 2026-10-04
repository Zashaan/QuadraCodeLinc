# Milestone 2: conversational benefits foundation

All supplied records are **SYNTHETIC DEMO DATA — NOT REAL LINCOLN DATA**.
No caller identity verification or real member integration is implemented.

## Tools and state

The existing authenticated session and tool endpoints are unchanged. Sessions advertise:

- `resolve_member_id`: literal spoken-ID parsing followed by repository validation.
- `get_member`: exact member facts, including DEMO001's stored $800 remaining maximum.
- `update_conversation_context`: bounded intent, procedure, provider, ZIP, five constraints,
  and latest correction. Omitted fields stay; null clears. Changing procedure clears the
  previous provider unless explicitly replaced. Member facts cannot be supplied here.
- `retrieve_plan_context`: source-backed excerpts scoped to the member's plan, employer,
  year and state; optional document type. No answer generation or arithmetic.
- `search_providers`: synthetic options filtered by plan, ZIP/radius, network, specialty
  and configured procedure aliases. Results use provider-ID order, not recommendation rank.
- `calculate_benefit`: one deterministic estimate using repository member facts and
  structured plan rules. Missing information yields named fields to ask about.

Nova interprets natural speech, corrections, greetings and follow-ups. There are no
sentence-matching conversation scripts. It keeps conversational context within the same
bounded seven-minute stream. The backend keeps bounded structured context, the latest
member result and up to four sources, not transcripts. State disappears on hangup,
expiry or restart. Use one backend worker. A successful answer does not close the call.
An explicit corrected ID span is passed verbatim to the resolver; unclear corrections
require repetition. Failed lookup clears member-specific state and never establishes
an unknown ID as a validated member. Tools for a session execute serially; overlapping
requests receive a safe 409 and may be retried.

Both the documented `contentEnd: INTERRUPTED` signal and the assistant JSON interruption
marker used in AWS's Nova 2 sample clear Twilio playback and suppress old-completion
frames. Duplicate signals clear once. A cancelled partial tool block is discarded without
closing the session. Normal `completionEnd` events do not end the stream.

## Calculator scope

The calculator is pure Python and uses Decimal; money is serialized as two-decimal USD
strings. Binary floats, non-finite values, negative amounts, amounts over $1,000,000 and
sub-cent input amounts are rejected. Plan payment rounds to cents using ROUND_HALF_UP.

For the supplied synthetic plan:

1. Eligible amount is the lesser of charge and allowed amount.
2. Apply the remaining deductible if the configured category requires it.
3. Multiply the remainder by the configured plan share; round once to cents.
4. Cap payment at the member's stored remaining annual maximum.
5. Member payment includes deductible, coinsurance, cap shortfall and any configured
   balance billing. In-network excess is a contractual write-off in this demo plan.

`amount_not_covered` means balance billing plus the annual-maximum shortfall; it excludes
ordinary deductible/coinsurance, which are included in `estimated_member_payment`.
`annual_maximum_consumed` equals plan payment. Estimates never mutate or reserve benefits.
Provider charge and allowed amount are separate required facts. Caller-supplied charge,
allowed amount and network need explicit source labels and result in an unverified-input
warning. Conflicting repository and caller network/allowed amounts require clarification.
No missing fee or allowed amount is automatically guessed or copied from the charge.

Procedure/category mappings, percentages, deductible applicability and balance-billing
rules live in `data/demo/plans.json`, not calculator logic. Waiting or frequency restrictions
that cannot be established return unverified rather than a fabricated estimate. The demo
explicitly has no waiting/frequency restriction for its three procedures. No multi-procedure
coordination, claims adjudication, FSA calculations or optimization are included.

The 2026 fixture contains balances as of 2026-10-04. Earlier dates and unsupported plan
years fail closed; later 2026 estimates assume no intervening claims or other insurance.

| Scenario | Plan pays | Member pays | Write-off | Maximum remaining |
| --- | ---: | ---: | ---: | ---: |
| DEMO001, crown, DEMO_P1, 2026-11-01 | $600 | $600 | $200 | $200 |
| DEMO001, root canal, DEMO_P3, 2026-11-01 | $800 | $600 | $200 | $0 |

These are independent estimates from the same stored balance, not sequential claims.

## Local data and approved document import

- `data/demo/members.json`: synthetic member records with plan/employer/year/state.
- `data/demo/plans.json`: reviewed structured rules; runtime retrieval never rewrites them.
- `data/demo/providers.json`: synthetic locations, explicit distances, fee/allowed amounts,
  network facts and verification metadata. Unknown distance is excluded from radius search.
- `data/demo/plan-content.json`: project-authored text labelled
  **SYNTHETIC DEMO PLAN CONTENT — NOT AN ACTUAL LINCOLN PLAN**. Lexical retrieval only.

Future approved documents should be staged outside source control in an approved storage
location and imported to the configured Knowledge Base with metadata sidecars. Do not
replace demo content with unreviewed text or imply it is Lincoln documentation. A developer
must separately review and encode calculator rules in the plan repository. Retain the
actual document identifier/name/section and page only when genuinely known. The existing
local repository constructors also accept developer-configured paths for fixture/import
validation; tool arguments never choose filesystem paths.

## Optional AWS setup (nothing provisioned automatically)

Local defaults need neither DynamoDB nor a Knowledge Base. Automated tests use synthetic
fixtures, SDK stubs and in-process HTTP; they do not read AWS credentials or contact AWS.

For an **existing synthetic demo DynamoDB table**:

1. Use a string partition key named `member_id`, no sort key. Load the synthetic JSON
   objects with their existing fields (including `synthetic: true` and ISO `balances_as_of`).
2. Grant only `dynamodb:GetItem` on that table to the selected AWS identity.
3. Set `MEMBER_REPOSITORY=dynamodb`, `DYNAMODB_MEMBER_TABLE` and `AWS_REGION` locally.
4. Use the AWS standard credential chain, such as an authenticated SSO profile selected
   through `AWS_PROFILE`. Never put AWS access keys in source or `.env`.

For an **existing Bedrock Knowledge Base**:

1. Import approved/demo source content and configure metadata for `plan_id`, `employer`,
   numeric `plan_year`, `state`, `doc_type`, `source_id`, `document`, and Boolean `synthetic`.
   Optional: `section`, numeric `page`, and original `uri`. Metadata must describe the source.
2. Ensure the KB's vector store supports the configured equality filters. The adapter sends
   an AND filter for all member scope fields and checks returned metadata again.
3. Grant `bedrock:Retrieve` on the selected Knowledge Base. Set `RAG_PROVIDER=bedrock`,
   `BEDROCK_KNOWLEDGE_BASE_ID`, `AWS_REGION` and, if needed, `AWS_PROFILE`.
4. Missing/malformed metadata or an unavailable service produces an unverified/unavailable
   result. There is no silent fallback to synthetic records or to a different plan.

SDK connect/read timeouts are one/two seconds with one total attempt. AWS calls run outside
the FastAPI event loop. Actual cloud permissions, metadata ingestion and live connectivity
remain manual acceptance checks. Do not create paid infrastructure merely to run tests.

## Checks and restart

Install both backend and gateway dependencies, then run `./scripts/check.sh`. Every stage
has a finite timeout. It covers backend tests, Ruff lint/format, mypy, gateway tests, Biome,
TypeScript, build, and `./scripts/smoke-benefits.sh`. Gateway contract tests invoke the Python
smoke with a 15-second limit; HTTP/WebSocket tests briefly open loopback sockets and close
them. No dev server or watcher is started by checks. The standalone benefits smoke requires
no running service. `./scripts/smoke-tool.sh` remains the optional check against a backend
that you already started.

After installation, restart **both** the FastAPI backend (`./scripts/run-backend.sh`) and
voice gateway (`npm --prefix voice-gateway run dev`, or build and use `npm start`). Keep the
existing tunnel if healthy; update `PUBLIC_BASE_URL` and Twilio's webhook only if its URL
changes. Existing calls use their old prompt/tool definitions: start a new call.

Phone acceptance is performed by the user: greet Abe naturally, ask an indirect balance
question, provide a spoken ID, ask a follow-up, correct the procedure, request a supported
estimate, interrupt it, and continue. Ask an unsupported question and confirm it requests
facts or says it cannot verify them. Automated protocol tests cannot establish live speech
recognition quality, barge-in latency or naturalness. No Milestone 2 phone pass is claimed.

## External contracts verified

- [Nova 2 output events](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-output-events.html)
- [Nova 2 interruption sample](https://github.com/aws-samples/amazon-nova-samples/blob/main/speech-to-speech/amazon-nova-2-sonic/sample-codes/console-python/nova_sonic_with_text.py)
- [DynamoDB GetItem](https://docs.aws.amazon.com/boto3/latest/reference/services/dynamodb/client/get_item.html)
- [Bedrock Retrieve](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agent-runtime/client/retrieve.html)

DynamoDB and Bedrock tests additionally validate requests/responses against installed
Botocore service models, without making network calls.
