"""Read-only discovery/acceptance of existing synthetic AWS resources. Never provisions."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config


def main() -> None:
    session = boto3.Session(region_name=os.environ.get("AWS_REGION", "us-east-1"))
    config = Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
    dynamo = session.resource("dynamodb", config=config)
    tables = {
        "members": "abe-members", "plans": "abe-plans", "coverage_rules": "abe-coverage-rules",
        "procedures": "abe-procedures", "providers": "abe-providers", "claims": "abe-claims",
        "authorizations": "abe-authorizations",
    }
    report = {"region": session.region_name, "tables": {}, "knowledge_bases": []}
    member = dynamo.Table(os.environ.get("DYNAMODB_MEMBER_TABLE", tables["members"])).get_item(
        Key={"member_id": "DEMO001"}, ConsistentRead=True
    ).get("Item")
    if not member or not member.get("is_synthetic", member.get("synthetic", False)):
        raise ValueError("Expected synthetic DEMO001 missing")
    report["member_check"] = {
        "found": True, "plan_id": member["plan_id"],
        "remaining": str(member["annual_maximum_remaining"]),
    }
    for label, table_name in tables.items():
        records = []
        options = {"Limit": 100}
        for _ in range(20):
            page = dynamo.Table(table_name).scan(**options)
            records.extend(page.get("Items", []))
            if not page.get("LastEvaluatedKey"):
                break
            options["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        else:
            raise ValueError("Demo dataset exceeds read bound")
        report["tables"][table_name] = {"count": len(records)}
        if label == "claims":
            report["member_claims"] = sum(x.get("member_id") == "DEMO001" for x in records)
        if label == "authorizations":
            report["member_authorizations"] = sum(x.get("member_id") == "DEMO001" for x in records)
        if label == "providers":
            report["provider_quotes_available"] = any(x.get("fees") for x in records)
    agent = session.client("bedrock-agent", config=config)
    for page in agent.get_paginator("list_knowledge_bases").paginate():
        for kb in page.get("knowledgeBaseSummaries", []):
            kid = kb["knowledgeBaseId"]
            for sources in agent.get_paginator("list_data_sources").paginate(knowledgeBaseId=kid):
                for summary in sources.get("dataSourceSummaries", []):
                    source = agent.get_data_source(knowledgeBaseId=kid, dataSourceId=summary["dataSourceId"])["dataSource"]
                    s3 = source.get("dataSourceConfiguration", {}).get("s3Configuration", {})
                    if s3.get("bucketArn", "").endswith(":rag-bucket123b"):
                        jobs = agent.list_ingestion_jobs(knowledgeBaseId=kid, dataSourceId=summary["dataSourceId"])["ingestionJobSummaries"]
                        report["knowledge_bases"].append({"id": kid, "status": kb["status"], "ingestion_statuses": [j["status"] for j in jobs]})
    from datetime import date
    from app.members.repository import DynamoDBMemberRepository
    from app.benefits.repository import DynamoDBPlanRulesRepository
    from app.providers.repository import DynamoDBProviderRepository, ProviderSearch
    from app.history.repository import DynamoDBHistoryRepository
    from app.optimization.models import OptimizationRequest
    from app.optimization.optimizer import optimize_benefits
    members = DynamoDBMemberRepository(tables["members"], session.region_name)
    model = members.get_member("DEMO001")
    plans = DynamoDBPlanRulesRepository(tables["plans"], tables["coverage_rules"], tables["procedures"], session.region_name)
    plan = plans.get_plan(model.plan_id, model.employer_id, model.plan_year)
    providers = DynamoDBProviderRepository(tables["providers"], session.region_name).search(model.plan_id, ProviderSearch())
    history = DynamoDBHistoryRepository(tables["claims"], tables["authorizations"], session.region_name).get_history(model.member_id)
    result = optimize_benefits(model, plan, providers, OptimizationRequest(procedure="crown", requested_date=date.today()), history=history)
    report["application_adapters"] = {"member_plan": model.plan_id, "plan_loaded": plan is not None,
        "crown_mapping": plan.procedure_id("crown"), "provider_count_bounded": len(providers),
        "claims_complete": history.claims_complete, "authorizations_complete": history.authorizations_complete,
        "optimizer_status": result.status, "missing_fields": result.missing_fields}
    print(json.dumps(report, indent=2))
    if not report["knowledge_bases"]:
        print("BLOCKED: no existing Knowledge Base attached to rag-bucket123b in this region.")
    if not report["provider_quotes_available"]:
        print("BLOCKED: provider quote/allowed-amount data absent; do not invent live estimates.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        print("AWS check failed:", error.response["Error"]["Code"])
        sys.exit(1)
    except Exception as error:
        print("AWS check failed:", type(error).__name__)
        sys.exit(1)
