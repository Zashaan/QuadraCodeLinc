"""Lazy AWS construction; local mode never loads credentials or creates clients."""

from typing import Any


def aws_client(
    service: str,
    region: str,
    *,
    resource: bool = False,
    connect_timeout: int = 1,
    read_timeout: int = 2,
) -> Any:
    import boto3  # type: ignore[import-untyped]
    from botocore.config import Config  # type: ignore[import-untyped]

    config = Config(
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        retries={"total_max_attempts": 1},
    )
    factory = boto3.resource if resource else boto3.client
    return factory(service, region_name=region, config=config)
