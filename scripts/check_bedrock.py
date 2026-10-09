"""Inspect account model access; invoke only with an explicit --invoke profile ID.

Uses existing AWS configuration. Prints status and usage, never credentials or
model output. Invocation is a small paid smoke check, not a model-quality test.
"""

import argparse
import json

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def inspect_models(client, model_ids):
    results = []
    for model_id in model_ids:
        try:
            result = client.get_foundation_model_availability(modelId=model_id)
            results.append({"model_id": model_id, **{key: result.get(key) for key in (
                "authorizationStatus", "entitlementAvailability", "regionAvailability", "agreementAvailability")}})
        except ClientError as error:
            results.append({"model_id": model_id, "error_code": error.response["Error"]["Code"]})
    return results


def smoke_invoke(client, model_id, max_output_tokens=16):
    if max_output_tokens not in {16, 64, 128, 256}:
        raise ValueError("Smoke checks require a bounded output budget.")
    result = client.converse(
        modelId=model_id, messages=[{"role": "user", "content": [{"text": "Reply with the word connected."}]}],
        inferenceConfig={"maxTokens": max_output_tokens},
    )
    content = result.get("output", {}).get("message", {}).get("content", [])
    return {"model_id": model_id, "status": "responded" if any(item.get("text") for item in content) else "no_text",
            "stop_reason": result.get("stopReason"), "usage": result.get("usage", {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[], help="Foundation model ID to inspect (read-only)")
    parser.add_argument("--profiles", action="store_true", help="List system inference profiles for inspected models")
    parser.add_argument("--invoke", help="Explicit profile/model ID for ONE bounded paid invocation")
    parser.add_argument("--max-output-tokens", type=int, choices=[16, 64, 128, 256], default=16)
    args = parser.parse_args()
    if not args.model and not args.invoke:
        parser.error("Supply --model for read-only checks or --invoke for a paid smoke check.")
    from saarthi_mcp.config import load_settings
    settings = load_settings()
    config = Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 1})
    try:
        client = boto3.client("bedrock", region_name=settings.aws_region, config=config)
        print(json.dumps({"region": settings.aws_region, "availability": inspect_models(client, args.model)}))
        if args.profiles:
            profiles = []
            for page in client.get_paginator("list_inference_profiles").paginate(typeEquals="SYSTEM_DEFINED"):
                for profile in page["inferenceProfileSummaries"]:
                    if any(model in profile["inferenceProfileId"] for model in args.model):
                        profiles.append({"id": profile["inferenceProfileId"], "status": profile["status"]})
            print(json.dumps({"profiles": profiles}))
        if args.invoke:
            runtime = boto3.client("bedrock-runtime", region_name=settings.aws_region, config=config)
            result = smoke_invoke(runtime, args.invoke, args.max_output_tokens)
            print(json.dumps(result))
            return 0 if result["status"] == "responded" else 2
        return 0
    except ClientError as error:
        print(json.dumps({"status": "failed", "error_code": error.response["Error"]["Code"],
                          "request_id": error.response.get("ResponseMetadata", {}).get("RequestId")}))
        return 1
    except BotoCoreError:
        print(json.dumps({"status": "failed", "error_code": "AWSConnectionOrCredentialsUnavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
