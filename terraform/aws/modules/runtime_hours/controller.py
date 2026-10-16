"""Reconcile the beta runtime with 07:00-23:00 Asia/Tokyo, without waiting in Lambda.

Invoked once a minute. The clock, never an event-supplied action, decides the state.
The dedicated API service must persist/cancel its work during graceful shutdown.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import boto3

JST = timezone(timedelta(hours=9), "Asia/Tokyo")


def reconcile(ecs, rds, config, now):
    if config["ENABLED"] != "true":
        return {"phase": "disabled"}
    local = now.astimezone(JST)
    open_hours = 7 <= local.hour < 23
    service_response = ecs.describe_services(
        cluster=config["CLUSTER_ARN"], services=[config["SERVICE_ARN"]]
    )
    if service_response.get("failures") or len(service_response.get("services", [])) != 1:
        raise RuntimeError("The configured beta service could not be identified")
    service = service_response["services"][0]
    if service["status"] != "ACTIVE":
        raise RuntimeError("The configured beta service is not active")
    db_response = rds.describe_db_instances(DBInstanceIdentifier=config["DB_IDENTIFIER"])
    if len(db_response["DBInstances"]) != 1:
        raise RuntimeError("The configured beta database could not be identified")
    database = db_response["DBInstances"][0]
    if database["DBInstanceArn"] != config["DB_ARN"]:
        raise RuntimeError("Database identity changed")
    status = database["DBInstanceStatus"]

    if open_hours:
        if status == "stopped":
            rds.start_db_instance(DBInstanceIdentifier=config["DB_IDENTIFIER"])
            return {"phase": "starting_database"}
        if status == "available":
            if service["desiredCount"] != 1:
                ecs.update_service(cluster=config["CLUSTER_ARN"], service=config["SERVICE_ARN"], desiredCount=1)
            return {"phase": "open", "running": service["runningCount"]}
        if status in {"starting", "stopping", "backing-up", "modifying", "maintenance", "rebooting"}:
            return {"phase": "waiting_database", "database": status}
        raise RuntimeError("Database needs operator attention: " + status)

    # ECS must finish its API task before RDS is stopped. No forced DB shutdown.
    if service["desiredCount"] != 0:
        ecs.update_service(cluster=config["CLUSTER_ARN"], service=config["SERVICE_ARN"], desiredCount=0)
        return {"phase": "stopping_api"}
    if service["runningCount"] or service["pendingCount"]:
        return {"phase": "draining_api"}

    # This is a dedicated cluster. Only the named disposable renderer family is stopped.
    tasks = []
    paginator = ecs.get_paginator("list_tasks")
    for page in paginator.paginate(cluster=config["CLUSTER_ARN"], family=config["RENDERER_FAMILY"], desiredStatus="RUNNING"):
        tasks.extend(page.get("taskArns", []))
    for task in tasks:
        ecs.stop_task(cluster=config["CLUSTER_ARN"], task=task, reason="Scheduled beta closure 23:00-07:00 Asia/Tokyo")
    if tasks:
        return {"phase": "stopping_renderers", "count": len(tasks)}

    if status == "available":
        rds.stop_db_instance(DBInstanceIdentifier=config["DB_IDENTIFIER"])
        return {"phase": "stopping_database"}
    if status == "stopped":
        return {"phase": "closed"}
    if status in {"starting", "stopping", "backing-up", "modifying", "maintenance", "rebooting"}:
        return {"phase": "waiting_database", "database": status}
    raise RuntimeError("Database needs operator attention: " + status)


def handler(event, context):
    # Delayed/replayed invocations cannot reopen the service during closed hours.
    result = reconcile(boto3.client("ecs"), boto3.client("rds"), os.environ,
                       datetime.now(JST))
    print(result)
    return result
