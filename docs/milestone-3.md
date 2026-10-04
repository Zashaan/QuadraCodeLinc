# Milestone 3: voice continuity and deterministic options

Milestone 3 extends the existing backend tools, repository interfaces, calculator and
voice gateway. The existing companion app is preserved; no UI feature or cloud resource
is provisioned by this milestone.

## Run and verify

From the repository root, after installing the existing requirements/lockfiles:

```bash
./scripts/check.sh
./scripts/verify-aws.sh
./scripts/run-backend.sh
# Separate terminal (reuse the existing ngrok process if already running):
ngrok http 3000
# Set PUBLIC_BASE_URL to that tunnel origin, then in another terminal:
npm --prefix voice-gateway run build
npm --prefix voice-gateway start
```

The AWS script only reads existing data. It prints counts, nonsecret resource metadata,
synthetic member balance checks and missing-data states. It never prints credentials,
places calls, changes DynamoDB data or provisions infrastructure. Keep `.env` ignored.

Local data modes remain the defaults. To read the existing AWS demo, configure:

```dotenv
AWS_PROFILE=codelinc
AWS_REGION=us-east-1
MEMBER_REPOSITORY=dynamodb
DYNAMODB_MEMBER_TABLE=abe-members
PLAN_RULES_SOURCE=dynamodb
DYNAMODB_PLAN_TABLE=abe-plans
DYNAMODB_COVERAGE_RULE_TABLE=abe-coverage-rules
DYNAMODB_PROCEDURE_TABLE=abe-procedures
PROVIDER_REPOSITORY=dynamodb
DYNAMODB_PROVIDER_TABLE=abe-providers
HISTORY_REPOSITORY=dynamodb
DYNAMODB_CLAIM_TABLE=abe-claims
DYNAMODB_AUTHORIZATION_TABLE=abe-authorizations
NOVA_VOICE_ID=matthew
```

Use `RAG_PROVIDER=bedrock` and the existing `BEDROCK_KNOWLEDGE_BASE_ID` once discovered.
For the uploaded documents without metadata sidecars, set
`RAG_DOCUMENT_MANIFEST_S3_URI=s3://rag-bucket123b/rag/metadata/documents.json`.
The adapter matches each retrieved S3 source against this manifest, includes plan titles
and ID in the query, prefers plan-specific evidence, accepts verified global material,
and drops conflicting or unknown sources. Structured rules remain the source of math.
With metadata sidecars and no manifest, the existing metadata-filtered adapter is retained.
`RAG_PROVIDER=disabled` is an explicit unavailable-evidence state when no KB exists; it
never silently substitutes another plan's local documents.

Read-only permissions required: DynamoDB GetItem for exact records, Query on coverage
rules, Scan on the small procedure/provider/history tables (the supplied tables have no
member GSIs), Bedrock Retrieve/ListKnowledgeBases/ListDataSources/GetDataSource/
ListIngestionJobs, and S3 GetObject for the configured manifest. Scans are bounded and
must finish completely before a history is marked complete. No write permissions are used.

## What was actually verified on October 4, 2026

The `codelinc` profile in `us-east-1` can read all seven supplied tables:
36 members, 3 plans, 12 category rules, 18 procedures, 45 providers, 61 claims and
4 authorizations. Application adapters dynamically resolve DEMO001 to HC-PLUS,
$1,500 maximum / $700 used / $800 remaining, its structured plan, providers and 3 claims.
These are synthetic HarborCare records, not actual Lincoln production members.

The S3 bucket contains the 12 source PDFs and its document manifest. The Bedrock API
returned **no Knowledge Bases in this region/account**, so no KB ID or ingestion job
could be verified. No KB was created. Supply the existing KB's region/account or create
and ingest one separately with approved infrastructure settings before enabling retrieval.

The AWS provider records contain no provider fee quotes, allowed amounts, distances or
appointment dates, and their network labels do not identify participation in a specific
plan. Member records omit FSA, location, balance-as-of and benefit-year fields; plans omit
benefit-year dates. Do not infer these facts from procedure typical prices, claim prices,
ZIP codes, current time or model-generated values. AWS-backed exact estimates/rankings
therefore report missing information until authoritative records provide the required
fields. Local fixture-based calculation and optimizer tests exercise complete inputs.

## Calculation and optimizer contracts

The single new `optimize_benefits` tool reuses the validated session member. It builds
at most 40 provider/date scenarios and applies hard constraints before scoring. Provider
search is independent; Python performs all financial arithmetic, FSA application and ranking.
Dates, deadlines, radius, network, specialty, active status, known availability, waiting
periods, authorization and relevant complete claim history can disqualify scenarios.
Missing distance cannot satisfy a hard radius; unknown availability cannot win fastest.

Calendar waiting periods, date/provider-scoped approval, supported frequency limits and
orthodontic lifetime caps extend the existing Decimal calculator. Unsupported rules fail
closed. No next-year dates are proposed without dentist-approved safe timing; urgency
prevents financial deferral. Continued future terms require an explicit assumption, and
annual/deductible resets do not reset lifetime usage. Source records are not mutated.
FSA is a funding source for member responsibility, never an insurance discount. Missing
FSA balance is reported as unknown; expiry and unverified carryover limit its use.

Default ranking balances member cost, elapsed days, travel and network. Explicit cheapest,
fastest, closest and immediate-cash priorities deterministically alter ranking. A distant
provider saving only $50 does not automatically win. Alternatives appear only when they
provide a material cost/time/distance difference; scores are not intended for spoken output.
Results include assumptions, exclusions, provenance and one next missing-information question.
Caller quotes are labeled unverified and cannot overwrite a known conflicting allowance.

## Barge-in defect and live acceptance

The old gateway permanently suppressed an interrupted `completionId`. Nova can reuse that
ID across conversational responses, leaving input alive but silencing subsequent replies.
The fix tracks individual response/content ownership, clears only interrupted playback,
ignores stale/duplicate interruption events, and keeps the input queue and Nova connection
open. Regression tests reuse completion IDs across A → interrupt → B → C, repeated
interruptions, early/late interruption and the actual Nova-to-Twilio adapter bridge.
Safe logs identify `playback_cleared`, input continuation and resumed assistant audio;
they contain no transcripts or audio content. See [protocol details](voice-protocol.md).

The configurable US-English masculine voice is `matthew`, verified against
[AWS Nova 2 Sonic voice documentation](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-language-support.html).
The prompt opens once with: “Hi, I'm Abe from Lincoln Financial, your AI benefits assistant.
How can I help?” It also identifies the data as a synthetic hackathon demo.

Automated tests and a direct Nova connection do not prove the real-call bug is fixed.
Use one short call to the existing number, +1 228-220-8820:

1. Let Abe speak, then interrupt: “Actually, I meant a root canal.” Expect old speech
   to stop and a natural response about the correction.
2. Interrupt a second response in the same call. The conversation should continue.
3. After the answer, ask “What plan am I on?” and give “demo double zero one” if needed.
   Expect HC-PLUS and the stored $800 if asking the remaining balance.
4. Ask a plan question after another interruption. If RAG remains disabled, Abe should
   explain that the plan-document detail cannot currently be verified.

Human acceptance is pending. No automated outbound call is made by these scripts.
