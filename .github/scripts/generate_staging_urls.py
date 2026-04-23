#!/usr/bin/env python3
"""
Generate a fresh pair of presigned S3 PUT URLs for the vp-staging bucket
and write them to staging-urls.json at the repo root.

Environment variables:
  S3_ACCESS  — EC2 access key
  S3_SECRET  — EC2 secret key
"""
import json
import os
import uuid
from datetime import datetime, timezone, timedelta

import boto3
from botocore.config import Config

ACCESS = os.environ["S3_ACCESS"]
SECRET = os.environ["S3_SECRET"]

ENDPOINT   = "https://js2.jetstream-cloud.org:8001"
BUCKET     = "vp-staging"
# GitHub's scheduled workflows are often delayed significantly, so keep
# presigned URLs valid long enough to survive missed 10-minute cron runs.
EXPIRES_IN = 7200  # 2 hours

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS,
    aws_secret_access_key=SECRET,
    config=Config(signature_version="s3v4"),
    region_name="RegionOne",
)

uid        = str(uuid.uuid4())
expires_at = (
    datetime.now(timezone.utc) + timedelta(seconds=EXPIRES_IN)
).strftime("%Y-%m-%dT%H:%M:%SZ")

json_url = s3.generate_presigned_url(
    "put_object",
    Params={"Bucket": BUCKET, "Key": f"{uid}.vp.json"},
    ExpiresIn=EXPIRES_IN,
)
png_url = s3.generate_presigned_url(
    "put_object",
    Params={"Bucket": BUCKET, "Key": f"{uid}.png"},
    ExpiresIn=EXPIRES_IN,
)

data = {
    "uuid":       uid,
    "json_url":   json_url,
    "png_url":    png_url,
    "expires_at": expires_at,
}

with open("staging-urls.json", "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")

print(f"Generated staging UUID : {uid}")
print(f"Expires at             : {expires_at}")
