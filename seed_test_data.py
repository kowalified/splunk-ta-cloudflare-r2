#!/usr/bin/env python3
"""
seed_test_data.py - Upload synthetic Cloudflare Logpush test data to R2
========================================================================
Uploads gzipped NDJSON files that mimic real Cloudflare Logpush output
for two datasets:
  - gateway_dns/    (sourcetype: cloudflare:dns)
  - http_requests/  (sourcetype: cloudflare:json)

Usage:
    python3 seed_test_data.py \
        --account-id  <32-char hex> \
        --access-key  <R2 access key id> \
        --secret-key  <R2 secret access key> \
        --bucket      <bucket name> \
        [--count 3]

Requirements: boto3  (pip3 install boto3)
"""

import argparse
import gzip
import io
import json
import random
import string
import sys
import time
from datetime import datetime, timezone, timedelta

import boto3
from botocore.config import Config


# ---------------------------------------------------------------------------
# Synthetic record generators
# ---------------------------------------------------------------------------

_DOMAINS = [
    "example.com", "google.com", "github.com", "cloudflare.com",
    "microsoft.com", "apple.com", "amazon.com", "stackoverflow.com",
]

# QueryType integers as used in real gateway_dns Logpush output
_QUERY_TYPES = [
    (1,  "A"),
    (28, "AAAA"),
    (5,  "CNAME"),
    (15, "MX"),
    (16, "TXT"),
    (33, "SRV"),
]
# Action field values in real gateway_dns output
_ACTIONS = ["allow", "allow", "allow", "block"]  # weight toward allow
_RESOLVER_DECISIONS = [
    "allowedOnNoPolicyMatch",
    "allowedByQueryName",
    "allowedByCategory",
    "blockedByQueryName",
    "allowedOnNoPolicyMatch",
    "allowedOnNoPolicyMatch",
]
_USER_EMAILS = [
    "alice@corp.example.com",
    "bob@corp.example.com",
    "charlie@corp.example.com",
]
_DEVICE_IDS = [
    "dad71818-0429-11ec-a0dc-{:012x}".format(random.randint(0, 2**48 - 1))
    for _ in range(5)
]

_HTTP_METHODS = ["GET", "POST", "GET", "GET", "PUT", "DELETE"]
_STATUS_CODES = [200, 200, 200, 301, 302, 400, 403, 404, 500, 200]
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "curl/7.88.1",
    "python-requests/2.28.0",
]


def _rand_str(n=8):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _rand_ip():
    return "{}.{}.{}.{}".format(
        random.randint(1, 254),
        random.randint(0, 255),
        random.randint(0, 255),
        random.randint(1, 254),
    )


def _rand_ray_id():
    return "{:016x}-IAD".format(random.randint(0, 2**64 - 1))


def make_gateway_dns_record(ts: datetime, account_id: str = "0" * 32) -> dict:
    """
    Mimic a Cloudflare Zero Trust Gateway DNS Logpush record.

    Field names and types match the REAL gateway_dns dataset schema from:
    https://developers.cloudflare.com/logs/logpush/logpush-job/datasets/account/gateway_dns/
    Retrieved via API: GET /accounts/{id}/logpush/datasets/gateway_dns/fields
    (80 fields total as of 2026-06-06)
    """
    domain = random.choice(_DOMAINS)
    resolved_ip = _rand_ip()
    qt_int, qt_name = random.choice(_QUERY_TYPES)
    resolver_decision = random.choice(_RESOLVER_DECISIONS)
    action = "block" if "blocked" in resolver_decision else random.choice(["allow", "allow", "allow"])

    # ResourceRecordsJSON is the current field; RData is deprecated but still emitted
    rr_json = json.dumps([{
        "name": domain + ".",
        "type": qt_name,
        "class": "IN",
        "ttl": random.randint(60, 3600),
        "rdata": resolved_ip if qt_name in ("A", "AAAA") else domain + ".",
    }])

    return {
        "AccountID": account_id,
        "ApplicationID": random.randint(0, 500),
        "ApplicationName": random.choice(["", "Google Search", "GitHub", "Cloudflare Dashboard"]),
        "AuthoritativeNameServerIPs": [_rand_ip()],
        "CNAMECategoryIDs": [],
        "CNAMECategoryNames": [],
        "CNAMEs": [],
        "CNAMEsReversed": [],
        "ColoCode": random.choice(["IAD", "LHR", "SJC", "NRT", "ORD"]),
        "ColoID": random.randint(1, 300),
        "CustomResolveDurationMs": 0,
        "CustomResolverAddress": "",
        "CustomResolverPolicyID": "",
        "CustomResolverPolicyName": "",
        "CustomResolverResponse": "",
        "Datetime": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),   # "Datetime" not "DateTime"
        "DeviceID": random.choice(_DEVICE_IDS),
        "DeviceName": "MacBook-{:04x}".format(random.randint(0, 0xFFFF)),
        "DoHSubdomain": "",
        "DoTSubdomain": "",
        "DstIP": _rand_ip(),
        "DstPort": 53,
        "EDEErrors": [],
        "Email": random.choice(_USER_EMAILS),
        "InitialCategoryIDs": [],
        "InitialCategoryNames": [],
        "InitialResolvedIPs": [resolved_ip],
        "InternalDNSDurationMs": random.randint(1, 50),
        "InternalDNSFallbackStrategy": "",
        "InternalDNSRCode": 0,
        "InternalDNSViewID": "",
        "InternalDNSZoneID": "",
        "IsResponseCached": random.choice([True, False]),
        "Location": "HQ-{:04x}".format(random.randint(0, 0xFFFF)),
        "LocationID": "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}".format(
            random.randint(0, 2**32-1), random.randint(0, 2**16-1),
            random.randint(0, 2**16-1), random.randint(0, 2**16-1),
            random.randint(0, 2**48-1)),
        "MatchedCategoryIDs": [],
        "MatchedCategoryNames": [],
        "MatchedIndicatorFeedIDs": [],
        "MatchedIndicatorFeedNames": [],
        "Policy": "",
        "PolicyID": "",
        "PolicyName": "",
        "Protocol": random.choice(["udp", "tcp", "doh"]),
        "QueryApplicationIDs": [],
        "QueryApplicationNames": [],
        "QueryCategoryIDs": [],
        "QueryCategoryNames": [],
        "QueryID": "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}".format(
            random.randint(0, 2**32-1), random.randint(0, 2**16-1),
            random.randint(0, 2**16-1), random.randint(0, 2**16-1),
            random.randint(0, 2**48-1)),
        "QueryIndicatorFeedIDs": [],
        "QueryIndicatorFeedNames": [],
        "QueryName": domain,
        "QueryNameReversed": ".".join(reversed(domain.split("."))),
        "QuerySize": random.randint(28, 120),
        "QueryType": qt_int,          # int, not string
        "QueryTypeName": qt_name,     # string version ("A", "AAAA", etc.)
        "RCode": 0,                   # int (0=NOERROR, 3=NXDOMAIN, etc.)
        "RData": [{"type": str(qt_int), "data": ""}],  # deprecated, kept for compat
        "RedirectTargetURI": "",
        "RegistrationID": random.choice(_DEVICE_IDS),
        "RequestContextCategoryIDs": [],
        "RequestContextCategoryNames": [],
        "ResolvedIPCategoryIDs": [],
        "ResolvedIPCategoryNames": [],
        "ResolvedIPContinentCodes": ["NA"],
        "ResolvedIPCountryCodes": ["US"],
        "ResolvedIPs": [resolved_ip],
        "ResolverDecision": resolver_decision,
        "ResolverPolicyID": "",
        "ResolverPolicyName": "",
        "ResourceRecords": [{"type": str(qt_int), "data": ""}],
        "ResourceRecordsJSON": rr_json,
        "ResponseTimeMs": random.randint(1, 200),
        "SrcIP": _rand_ip(),
        "SrcIPContinentCode": "NA",
        "SrcIPCountryCode": "US",
        "SrcPort": random.randint(1024, 65535),
        "TenantID": "",
        "TimeZone": "UTC",
        "TimeZoneInferredMethod": "orDefault",
        "UserID": "{:08x}-{:04x}-{:04x}-{:04x}-{:012x}".format(
            random.randint(0, 2**32-1), random.randint(0, 2**16-1),
            random.randint(0, 2**16-1), random.randint(0, 2**16-1),
            random.randint(0, 2**48-1)),
    }


def make_http_request_record(ts: datetime, zone_name: str = "example.com") -> dict:
    """Mimic a Cloudflare zone-level HTTP request log record."""
    path = random.choice(["/", "/api/v1/users", "/static/app.js", "/health", "/login"])
    method = random.choice(_HTTP_METHODS)
    status = random.choice(_STATUS_CODES)
    return {
        "BotScore": random.randint(1, 99),
        "BotScoreSrc": "Verified Bot",
        "CacheCacheStatus": random.choice(["hit", "miss", "expired", "bypass"]),
        "CacheResponseBytes": random.randint(100, 50000),
        "CacheResponseStatus": status,
        "CacheTieredFill": False,
        "ClientASN": random.randint(1, 65000),
        "ClientCountry": random.choice(["US", "GB", "DE", "JP", "CA"]),
        "ClientDeviceType": "desktop",
        "ClientIP": _rand_ip(),
        "ClientIPClass": "noRecord",
        "ClientRequestBytes": random.randint(200, 2000),
        "ClientRequestHost": zone_name,
        "ClientRequestMethod": method,
        "ClientRequestPath": path,
        "ClientRequestProtocol": "HTTP/2",
        "ClientRequestReferer": "",
        "ClientRequestScheme": "https",
        "ClientRequestSource": "eyeball",
        "ClientRequestURI": path,
        "ClientRequestUserAgent": random.choice(_USER_AGENTS),
        "ClientSSLCipher": "AEAD-AES256-GCM-SHA384",
        "ClientSSLProtocol": "TLSv1.3",
        "ClientSrcPort": random.randint(1024, 65535),
        "ClientTCPRTTMs": random.randint(1, 200),
        "ClientXRequestedWith": "",
        "Datetime": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "EdgeColoCode": random.choice(["IAD", "LHR", "SJC"]),
        "EdgeColoID": random.randint(1, 300),
        "EdgeEndTimestamp": ts.isoformat() + "+00:00",
        "EdgePathingOp": "wl",
        "EdgePathingSrc": "macro",
        "EdgePathingStatus": "nr",
        "EdgeRateLimitAction": "",
        "EdgeRateLimitID": 0,
        "EdgeRequestHost": zone_name,
        "EdgeResponseBodyBytes": random.randint(100, 50000),
        "EdgeResponseBytes": random.randint(200, 60000),
        "EdgeResponseCompressionRatio": round(random.uniform(1.0, 4.0), 2),
        "EdgeResponseContentType": "text/html",
        "EdgeResponseStatus": status,
        "EdgeServerIP": _rand_ip(),
        "EdgeStartTimestamp": ts.isoformat() + "+00:00",
        "EdgeTimeToFirstByteMs": random.randint(1, 500),
        "FirewallMatchesActions": [],
        "FirewallMatchesRuleIDs": [],
        "FirewallMatchesSources": [],
        "OriginDNSResponseTimeMs": random.randint(0, 50),
        "OriginIP": _rand_ip(),
        "OriginResponseBytes": random.randint(100, 50000),
        "OriginResponseHTTPExpires": "",
        "OriginResponseHTTPLastModified": "",
        "OriginResponseStatus": status,
        "OriginResponseTime": random.randint(1000000, 500000000),
        "OriginSSLProtocol": "TLSv1.3",
        "ParentRayID": "00" * 8,
        "RayID": _rand_ray_id(),
        "SecurityLevel": "med",
        "SmartRouteColoID": 0,
        "UpperTierColoID": 0,
        "WAFAction": "unknown",
        "WAFFlags": "0",
        "WAFMatchedVar": "",
        "WAFProfile": "unknown",
        "WAFRuleID": "",
        "WAFRuleMessage": "",
        "WorkerCPUTime": 0,
        "WorkerStatus": "unknown",
        "WorkerSubrequest": False,
        "WorkerSubrequestCount": 0,
        "ZoneID": random.randint(100000000, 999999999),
        "ZoneName": zone_name,
    }


# ---------------------------------------------------------------------------
# File generation
# ---------------------------------------------------------------------------

def make_log_file(records: list) -> bytes:
    """Serialize a list of dicts as gzipped NDJSON bytes."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        for rec in records:
            gz.write((json.dumps(rec, separators=(",", ":")) + "\n").encode("utf-8"))
    return buf.getvalue()


def logpush_key(prefix: str, dt: datetime, batch_index: int) -> str:
    """
    Generate a Logpush-style object key matching the REAL Logpush filename format.

    Real format observed from live Logpush R2 output (2026-06-07):
      {PREFIX}/{YYYYMMDD}/{START}_{END}_{RANDOM}.log.gz
      e.g. http_requests_live/20260607/20260607T011017Z_20260607T011055Z_6eea5f31.log.gz

    Start and end timestamps represent the time range of log records in the batch.
    We simulate a ~30-second batch window matching Logpush's ~30s flush interval.
    """
    end_dt = dt + timedelta(seconds=random.randint(5, 45))
    start_str = dt.strftime("%Y%m%dT%H%M%SZ")
    end_str = end_dt.strftime("%Y%m%dT%H%M%SZ")
    rand_hex = "{:08x}".format(random.randint(0, 0xFFFFFFFF))
    filename = "{}_{}_{}.log.gz".format(start_str, end_str, rand_hex)
    date_folder = dt.strftime("%Y%m%d")          # YYYYMMDD (no dashes - matches real format)
    return "{}{}/{}".format(prefix, date_folder, filename)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Seed R2 bucket with synthetic Cloudflare log files")
    parser.add_argument("--account-id", required=True, help="Cloudflare account ID (32-char hex)")
    parser.add_argument("--access-key", required=True, help="R2 access key ID")
    parser.add_argument("--secret-key", required=True, help="R2 secret access key")
    parser.add_argument("--bucket", required=True, help="R2 bucket name")
    parser.add_argument("--count", type=int, default=3,
                        help="Number of files per dataset (default: 3)")
    parser.add_argument("--records-per-file", type=int, default=20,
                        help="Log records per file (default: 20)")
    args = parser.parse_args()

    # Build R2 client
    endpoint = "https://{}.r2.cloudflarestorage.com".format(args.account_id)
    client = boto3.client(
        service_name="s3",
        endpoint_url=endpoint,
        aws_access_key_id=args.access_key,
        aws_secret_access_key=args.secret_key,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )

    # Verify bucket access
    try:
        client.head_bucket(Bucket=args.bucket)
        print("[OK] Connected to R2 bucket: {}".format(args.bucket))
    except Exception as exc:
        print("[ERROR] Cannot access bucket {}: {}".format(args.bucket, exc))
        sys.exit(1)

    now = datetime.now(timezone.utc)
    uploaded = []

    # --- Gateway DNS files ---
    print("\nUploading gateway_dns/ files...")
    for i in range(args.count):
        dt = now - timedelta(minutes=(args.count - i) * 5)
        records = [make_gateway_dns_record(dt, account_id=args.account_id)
                   for _ in range(args.records_per_file)]
        data = make_log_file(records)
        key = logpush_key("gateway_dns/", dt, i)
        client.put_object(Bucket=args.bucket, Key=key, Body=data,
                          ContentType="application/gzip")
        print("  uploaded {} ({} bytes, {} records)".format(key, len(data), len(records)))
        uploaded.append(key)

    # --- HTTP request files ---
    print("\nUploading http_requests/ files...")
    for i in range(args.count):
        dt = now - timedelta(minutes=(args.count - i) * 5)
        records = [make_http_request_record(dt) for _ in range(args.records_per_file)]
        data = make_log_file(records)
        key = logpush_key("http_requests/", dt, i)
        client.put_object(Bucket=args.bucket, Key=key, Body=data,
                          ContentType="application/gzip")
        print("  uploaded {} ({} bytes, {} records)".format(key, len(data), len(records)))
        uploaded.append(key)

    print("\n[DONE] Uploaded {} files ({} gateway_dns + {} http_requests)".format(
        len(uploaded), args.count, args.count))
    print("\nVerify with:")
    print("  wrangler r2 object list {}".format(args.bucket))
    print("\nOr with AWS CLI:")
    print("  AWS_ACCESS_KEY_ID={} AWS_SECRET_ACCESS_KEY=<secret> \\".format(args.access_key))
    print("  aws s3 ls s3://{}/ --endpoint-url {} --region auto --recursive".format(
        args.bucket, endpoint))


if __name__ == "__main__":
    main()
