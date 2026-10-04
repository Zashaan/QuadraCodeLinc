# Milestone 2 data and AWS adapters

All committed members, plans, provider records, and plan chunks are synthetic project-authored fixtures. They are not Lincoln data, real dentists, PHI, or coverage promises.

## Sources of truth

- `MemberRepository`: exact member and balance facts. `synthetic` reads `data/demo/members.json`; `dynamodb` reads one explicitly configured table.
- `PlanRulesRepository`: typed arithmetic rules from `data/demo/plans.json`. Procedure category and alias mappings live here, not in the calculator.
- `PlanDocumentRetriever`: explanatory evidence. `local` performs deterministic term-overlap retrieval over `data/demo/rag/plan_chunks.json`; `bedrock` calls the Bedrock Knowledge Bases Retrieve API with plan metadata filters.
- `ProviderRepository`: provider/network/location/fee facts from `data/demo/providers.json`. Search only filters fixture order and does not optimize.
- `BenefitCalculator`: pure Python `Decimal` arithmetic. It does not call Nova, an LLM, a retriever, or a provider search.

Approved plan files should later be staged outside `data/demo/`, reviewed for authorization and metadata, and ingested into the configured Bedrock Knowledge Base. Do not replace the synthetic fixture with unapproved customer documents. Required metadata keys are `source_document`, `source_id`, `plan_id`, `employer`, `plan_year`, `state`, and `doc_type`; `section` and `page` are optional and must only be included when known.

## DynamoDB setup (manual)

Set `MEMBER_REPOSITORY=dynamodb` and `DYNAMODB_MEMBER_TABLE` to an existing table whose string partition key is `member_id`. Store the fields represented by the typed `Member` model using DynamoDB `S`/`N` attributes. Grant the backend identity only `dynamodb:GetItem` on that table. The adapter uses consistent reads and the AWS SDK credential chain. It never falls back to synthetic records when DynamoDB mode is selected.

No table is provisioned or seeded automatically. Keep this demo synthetic unless identity verification, data authorization, privacy controls, and production integration have been approved.

## Bedrock Knowledge Base setup (manual)

Create or select a Knowledge Base and data source outside this project, ingest only approved plan content with the metadata above, and grant the backend identity `bedrock:Retrieve` for that Knowledge Base. Set `RAG_PROVIDER=bedrock`, `BEDROCK_KNOWLEDGE_BASE_ID`, and `AWS_REGION`. The adapter applies available member plan/employer/year/state/document-type filters and preserves returned source metadata. It does not provision a Knowledge Base, OpenSearch, or other paid infrastructure.

Local development uses `MEMBER_REPOSITORY=synthetic`, `PLAN_RULES_SOURCE=local`, `RAG_PROVIDER=local`, and `PROVIDER_REPOSITORY=synthetic`, so tests and the benefits smoke test need no AWS credentials.
