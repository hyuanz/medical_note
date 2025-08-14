import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError

# ---------------------------
# Helpers
# ---------------------------

def info(msg: str):
    print(f"[INFO] {msg}")

def warn(msg: str):
    print(f"[WARN] {msg}")

def err(msg: str):
    print(f"[ERR ] {msg}", file=sys.stderr)


# ---------------------------
# AWS setup
# ---------------------------


def ensure_table(dynamodb, table_name: str):
    """
    Create DynamoDB table if not exists.
    PK: summarize_job_name (S)
    GSI: patient_id (S)
    Billing: PAY_PER_REQUEST (on-demand)
    """
    try:
        dynamodb.describe_table(TableName=table_name)
        info(f"DynamoDB table exists: {table_name}")
        return
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    info(f"Creating DynamoDB table: {table_name}")
    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "summarize_job_name", "AttributeType": "S"},
            {"AttributeName": "patient_id", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "summarize_job_name", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "patient_id-index",
                "KeySchema": [{"AttributeName": "patient_id", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = dynamodb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    info("Table created and active.")


# ---------------------------
# JSON normalization (Dynamo-style -> plain dict)
# ---------------------------

def _from_ddb_attr(attr: Any) -> Any:
    """Convert a DynamoDB AttributeValue (e.g., {"S": "x"}) to a Python value."""
    if not isinstance(attr, dict) or len(attr) != 1:
        return attr
    (t, v), = attr.items()
    if t == "S":
        return v
    if t == "N":
        try:
            # try int first, else float
            if isinstance(v, str):
                if v.isdigit() or (v.startswith('-') and v[1:].isdigit()):
                    return int(v)
            return float(v)
        except Exception:
            return v
    if t == "BOOL":
        return bool(v)
    if t == "NULL":
        return None
    if t == "L":
        return [_from_ddb_attr(x) for x in v]
    if t == "M":
        return {k: _from_ddb_attr(x) for k, x in v.items()}
    return v


def normalize_record(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Turn either a Dynamo AttributeValue map OR a plain dict into a plain dict."""
    if any(
        isinstance(v, dict) and len(v) == 1 and next(iter(v)) in ("S", "N", "BOOL", "L", "M", "NULL")
        for v in obj.values()
    ):
        return {k: _from_ddb_attr(v) for k, v in obj.items()}
    return obj


# ---------------------------
# DDB write helpers
# ---------------------------

def to_ddb_item(d: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Convert a normal dict to DynamoDB AttributeValue map."""
    def conv(v: Any) -> Dict[str, Any]:
        if v is None:
            return {"NULL": True}
        if isinstance(v, bool):
            return {"BOOL": v}
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return {"N": str(v)}
        if isinstance(v, str):
            return {"S": v}
        if isinstance(v, list):
            return {"L": [conv(x) for x in v]}
        if isinstance(v, dict):
            return {"M": {k: conv(x) for k, x in v.items()}}
        return {"S": str(v)}

    return {k: conv(v) for k, v in d.items()}


def batch_write_items(dynamodb, table_name: str, records: List[Dict[str, Any]], batch_size: int = 25, max_retries: int = 5):
    """Batch write many items with basic retry on UnprocessedItems."""
    chunks = [records[i : i + batch_size] for i in range(0, len(records), batch_size)]
    for i, chunk in enumerate(chunks, 1):
        request_items = {
            table_name: [
                {"PutRequest": {"Item": to_ddb_item(rec)}}
                for rec in chunk
            ]
        }
        backoff = 0.2
        for attempt in range(max_retries):
            resp = dynamodb.batch_write_item(RequestItems=request_items)
            unprocessed = resp.get("UnprocessedItems", {})
            count = sum(len(v) for v in unprocessed.values())
            if count == 0:
                break
            warn(f"{count} unprocessed items, retrying (attempt {attempt+1})")
            time.sleep(backoff)
            backoff = min(backoff * 2, 5)
            request_items = unprocessed
        info(f"Wrote batch {i}/{len(chunks)} ({len(chunk)} items)")


# ---------------------------
# Main
# ---------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default=os.getenv("AWS_REGION", "us-west-2"))
    ap.add_argument("--table", required=True)
    ap.add_argument("--test-object", default="connectivity.txt")
    ap.add_argument("--import-file", help="Path to JSON list of objects (Dynamo-style or plain)")
    ap.add_argument("--dry-run", action="store_true", help="Only check creds and exit")
    args = ap.parse_args()

    # Clients
    session = boto3.Session(region_name=args.region)
    s3 = session.client("s3")
    ddb = session.client("dynamodb")

    # Creds check
    sts = session.client("sts")
    ident = sts.get_caller_identity()
    info(f"AWS connected as Account {ident['Account']}, ARN {ident['Arn']} in {args.region}")

    ensure_table(ddb, args.table)

    if args.dry_run:
        info("Dry-run complete. Infra verified.")
        return

    # Optional import
    if args.import_file:
        info(f"Importing sample file: {args.import_file}")
        try:
            with open(args.import_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            err(f"Failed to read JSON: {e}")
            sys.exit(2)

        if not isinstance(data, list):
            err("Import file must be a JSON list of objects")
            sys.exit(2)
    
        records = [normalize_record(x) for x in data]

        # Ensure PK present
        before = len(records)
        records = [r for r in records if "summarize_job_name" in r]
        missing_count = before - len(records)
        if missing_count:
            warn(f"{missing_count} record(s) missing 'summarize_job_name' skipped")

        # Coerce patient_id to str to match GSI type S
        for r in records:
            if "patient_id" in r and not isinstance(r["patient_id"], str):
                r["patient_id"] = str(r["patient_id"])

        if not records:
            warn("No valid records to import.")
        else:
            batch_write_items(ddb, args.table, records)
            info("Import complete. Example query:")
            example_key = records[0]["summarize_job_name"]
            print(
                json.dumps(
                    {
                        "GetItem": {
                            "TableName": args.table,
                            "Key": {"summarize_job_name": {"S": example_key}},
                        }
                    },
                    indent=2,
                )
            )

    info("All done. You can now query by summarize_job_name or via GSI patient_id-index.")


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        err(f"AWS error: {e}")
        sys.exit(2)
    except Exception as e:
        err(f"Unexpected error: {e}")
        sys.exit(1)
