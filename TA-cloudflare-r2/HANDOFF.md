# Cloudflare R2 Log Ingestion Add-on for Splunk - Handoff Document

**Version**: 0.1.0 (reference prototype)  
**Date**: 2026-06-07  
**Author**: Michael Kowal, Senior Solutions Engineer, Cloudflare  
**Intended recipients**: Cloudflare Splunk Partnership Team, third-party vendor

---

## Purpose

This document accompanies a working reference prototype of a Splunk Technology
Add-on (TA) that ingests Cloudflare Logpush log files from Cloudflare R2 via a
Python modular input.

The prototype exists to:
1. Prove the technical approach is sound and functional
2. Provide a head start for the official vendor-productionized version
3. Document all design decisions, known gaps, and AppInspect findings

**This prototype is NOT ready for direct Splunkbase publication.** See the
IP/legal section below.

---

## Background and Problem Statement

The Splunk Add-on for AWS (Splunkbase 1876) does not work with Cloudflare R2.
The add-on calls `sts.get_caller_identity()` against AWS STS to validate
credentials before saving them. R2 is S3-compatible but has no STS service,
so the add-on rejects R2 credentials before making any S3 call.

This is a confirmed dead-end as of Splunk Add-on for AWS v8.1.2 (validated
2026-06-01 against Cloudflare internal wiki documentation by Rian van der Merwe).
No configuration change or version downgrade resolves it.

**Specifically: the Generic S3 input with `host_name` / `s3_private_endpoint_url`
also does not work.** These parameters change where S3 API calls go, but the
`aws_account` credential layer - shared by ALL input types - calls
`sts.get_caller_identity()` both at UI account creation AND at runtime. Tested
and confirmed: manually editing `inputs.conf` with `host_name` pointing at the
R2 endpoint still produces the same STS error at runtime. There is no supported
path through the AWS TA to reach R2.

The existing Cloudflare App for Splunk (Splunkbase 4501) provides dashboards
and field extractions but does NOT handle data ingestion. It assumes data is
already in Splunk (typically via Logpush → HEC). For customers where Splunk is
on-premises and not reachable from the internet (e.g., air-gapped banks), there
is currently no supported ingestion path from R2 to Splunk.

This TA fills that gap.

---

## What Was Built and Tested

### Architecture

```
Cloudflare R2 bucket
  (Logpush-written gzipped NDJSON files)
         |
         | ListObjectsV2 (start-after checkpoint)
         | GetObject + gunzip
         v
  Python modular input (cloudflare_r2://)
         |
         | XML event stream (one event per JSON line)
         v
  Splunk indexer
         |
         v
  index=<user-configured>  sourcetype=<user-configured>
```

### What was validated end-to-end

- **R2 connectivity**: ListObjectsV2, GetObject via boto3 with SigV4 auth,
  path-style addressing, endpoint `https://<account_id>.r2.cloudflarestorage.com`
- **Log format**: gzipped NDJSON parsed correctly; one Splunk event per JSON line
- **Two real Logpush datasets**: `http_requests` (zone-level, `cloudflare:json`)
  and schema-accurate synthetic `gateway_dns` (`cloudflare:dns`)
- **Checkpointing**: `ListObjectsV2` `StartAfter` parameter tracks last processed
  key; checkpoint file persists across Splunk restarts; zero duplicate events
  confirmed across multiple restart cycles
- **Multi-input**: Two simultaneous input instances (different prefixes/sourcetypes)
  run independently with separate checkpoint files
- **Splunk version**: 10.4.0 (Docker, linux/amd64)
- **Python version**: 3.9 (Splunk-bundled), via vendored boto3 1.42.97

### Dataset agnosticism

The TA is intentionally dataset-agnostic. It treats all Logpush output as opaque
gzipped NDJSON. `sourcetype`, `index`, and `key_prefix` are per-input parameters
set by the user. The TA does no field extraction or dataset-specific processing.

For alignment with the Cloudflare App for Splunk (4501), users should configure:

| Dataset | key_prefix | sourcetype |
|---|---|---|
| Zone HTTP Requests | `http_requests/` | `cloudflare:json` |
| Gateway DNS | `gateway_dns/` | `cloudflare:dns` |
| Gateway HTTP | `gateway_http/` | `cloudflare:http` |
| Gateway Network | `gateway_network/` | `cloudflare:network` |
| Access | `access/` | `cloudflare:access` |
| Audit | `audit/` | `cloudflare:audit` |

These are recommendations, not enforced by the TA.

---

## File Structure

```
TA-cloudflare-r2/
├── default/
│   ├── app.conf               # App metadata (version, label, author)
│   └── inputs.conf            # Default stanza (disabled, interval=300)
├── README/
│   └── inputs.conf.spec       # Modular input parameter definitions
├── bin/
│   ├── cloudflare_r2.py       # The modular input (single file, ~350 lines)
│   └── lib/                   # Vendored Python dependencies
│       ├── boto3/             # AWS SDK (S3 client)
│       ├── botocore/          # boto3 dependency (S3 only; other services pruned)
│       │   └── data/
│       │       ├── s3/        # S3 service definitions
│       │       └── sts/       # Required for endpoint resolution
│       ├── s3transfer/        # boto3 dependency
│       ├── urllib3/           # HTTP client (1.26.x for Python 3.9 compat)
│       ├── jmespath/          # boto3 dependency
│       ├── dateutil/          # boto3 dependency
│       ├── six.py             # Python 2/3 compat shim (boto3 dep)
│       └── splunklib/         # Splunk Python SDK (modularinput framework)
├── metadata/
│   └── default.meta           # Access control metadata
└── LICENSE                    # Apache 2.0 + third-party notices
```

---

## Input Parameters

Configured per-input instance in `inputs.conf` or via Splunk UI:

| Parameter | Required | Description |
|---|---|---|
| `account_id` | Yes | 32-char hex Cloudflare account ID |
| `access_key_id` | Yes | R2 S3-compatible API access key ID |
| `secret_access_key` | Yes | R2 S3-compatible API secret access key |
| `bucket_name` | Yes | R2 bucket name |
| `key_prefix` | No | Key prefix to scope to one dataset (e.g. `gateway_dns/`) |
| `interval` | Yes | Polling interval in seconds (default: 300) |
| `verify_ssl` | No | SSL cert verification (default: true; see note below) |
| `index` | No | Splunk index (default: main) |
| `sourcetype` | No | Splunk sourcetype (see table above) |

**R2 API token requirements**: The token used for `access_key_id` /
`secret_access_key` needs **Object Read** permission scoped to the target bucket.
Write permission is not required for the TA (only needed for the seed script).

---

## Checkpointing Design

**How it works**: After successfully processing each `.log.gz` file, the TA
writes a checkpoint file containing the S3 key of that file. On the next poll,
`ListObjectsV2` is called with `StartAfter=<last_key>`, which returns only keys
lexicographically after the checkpoint. Since Logpush key names are
timestamp-prefixed, this reliably returns only new files.

**Why this works for Logpush**: Logpush is write-once. Each batch writes a new
file with a new key. It never appends to or modifies existing files. There is no
need for byte-offset tracking within a file.

**At-least-once delivery**: If Splunk crashes after partially processing a file
but before saving the checkpoint, that file will be re-processed on restart.
This causes duplicate events for the records already indexed. Mitigation:
deduplicate at search time using `RayID` (HTTP requests) or `QueryID` (Gateway
DNS) via `stats count by RayID` or Splunk's `dedup` command.

**Checkpoint file location**: `$SPLUNK_HOME/var/lib/splunk/modinputs/cloudflare_r2/`
One file per input instance, named `<sanitized_stanza_name>.json`.

---

## AppInspect Status

Run command: `splunk-appinspect inspect TA-cloudflare-r2-0.1.0.tgz --mode precert`

| Category | Count | Notes |
|---|---|---|
| Errors | 0 | |
| Failures | 0 | Clean pass |
| Future failures | 0 | |
| Warnings | 6 | All from vendored libraries, not TA code |
| Successes | 90 | |

**The package passes AppInspect precert with no failures.**

### Warnings summary

| Warning | Source | Action |
|---|---|---|
| 246 Python files | botocore data files (not executable) | No action; false positive |
| subprocess usage | botocore internals | No action; vendored library |
| Outdated Splunk SDK | splunklib 2.1.1 | Update to latest before Splunkbase submission |
| admin role in default.meta | default.meta | Update to appropriate Splunk Cloud roles |
| inputs.conf not synced to indexers | Expected for modular inputs | No action needed |
| No setup page | modular input has no UI page | See gaps below |

---

## Known Gaps for Productionization

These items are NOT present in the prototype and MUST be addressed before
Splunkbase submission:

### 1. Credential management UI (HIGH PRIORITY)

Currently, `access_key_id` and `secret_access_key` are stored as plain text in
`inputs.conf`. The production version needs:

- A setup page (`default/data/ui/nav/default.xml` + REST handler) that accepts
  credentials via the Splunk UI and stores them in Splunk's encrypted credential
  store (`passwords.conf` via `storage/passwords`)
- The modular input should read credentials via `self.service.storage_passwords`
  rather than from `input_item` directly

Reference implementation: `Splunk_TA_aws` credential management pattern.

### 2. Python 3.13 and Splunk 9.x compatibility

The prototype was built and tested on Splunk 10.4.0 with Python 3.9. Before
Splunkbase submission:

- Test on Splunk 9.x (Python 3.7 baseline)
- Update `splunklib` to latest version
- Verify boto3 compatibility with Python 3.13 (boto3 has dropped 3.9 support)
- Consider shipping boto3 via the `.dependencies/` mechanism introduced in
  Splunk 9.0 (cleaner than bin/lib/ for large dependencies)

### 3. Splunk Cloud compatibility

The current checkpoint path fallback (`SPLUNK_HOME`-derived path) should be
reviewed for Splunk Cloud. In Splunk Cloud, `SPLUNK_HOME` may not be set, and
the modinputs directory path differs. Test against Splunk Cloud (Victoria
experience) specifically.

### 4. SSL/TLS handling

The `verify_ssl` parameter exists for environments with TLS inspection proxies.
In production, the correct fix is to add the inspection CA certificate to the
Splunk server's trust store rather than disabling verification. Consider adding
a `ca_bundle` parameter that accepts a path to a CA bundle file.

### 5. Error handling and observability

The prototype logs to `splunkd.log` via `ew.log()`. Production should add:
- Structured logging for monitoring/alerting
- A health check endpoint or dashboard that shows last poll time, files
  processed, and any errors
- Configurable log level parameter

### 6. Rate limiting and large bucket handling

The prototype uses `MaxKeys=1000` per ListObjectsV2 call. For high-volume
accounts with many files, consider:
- Configurable `max_keys` parameter
- Configurable `max_files_per_poll` to limit per-interval ingestion volume
- Back-pressure handling if Splunk indexer is slow

### 7. Splunkbase listing requirements

Before submitting to Splunkbase:
- App icon (200x200 PNG, `static/appIcon.png` and `static/appIcon_2x.png`)
- Screenshots
- Splunkbase description and tags
- Confirmed Cloudflare legal/management approval (see IP/legal section)

---

## IP and Legal Notice

**IMPORTANT**: This add-on was written by a Cloudflare employee (Michael Kowal)
as part of customer-facing technical work for Cloudflare. Code produced by a
Cloudflare employee in the course of their work is Cloudflare intellectual
property.

**Do not publish this add-on to Splunkbase under a personal account.**

Any public release - whether under the author's name or under a Cloudflare
brand - requires sign-off from:
- Cloudflare Legal (IP assignment, open-source licensing)
- Cloudflare Management (approval to publish)

The intended path is:
1. Hand this prototype to the Cloudflare Splunk partnership team
2. Partnership team engages the existing third-party vendor (who maintains the
   Cloudflare App for Splunk, Splunkbase 4501)
3. Vendor productionizes and publishes under the established partnership channel

**Internal stakeholders to engage**:
- Michael McGrory (mmcgrory@cloudflare.com) - Partner SE, Splunk app SME
- Morgan Steffen (msteffen@cloudflare.com) - Partner Success Manager
- Gavin Chen (gavin@cloudflare.com) - Partner Development Manager
- Chris Shelley - Engineering manager (already briefed on the gap)

**Related SFDC feature request**: a0dNv00000FU3RFIA1

---

## Testing Instructions

### Prerequisites

- Splunk Enterprise 10.x (Docker: `docker pull --platform linux/amd64 splunk/splunk:latest`)
- Cloudflare account with R2 enabled
- R2 bucket with Logpush data (or use `seed_test_data.py` to generate synthetic data)
- R2 API token with Object Read permission on the target bucket

### Quick start

1. Install the TA:
   ```bash
   # Via Splunk UI: Apps > Manage Apps > Install app from file
   # Or direct mount for development:
   docker run -v $(pwd)/TA-cloudflare-r2:/opt/splunk/etc/apps/TA-cloudflare-r2 ...
   ```

2. Create an input via `local/inputs.conf`:
   ```ini
   [cloudflare_r2://my_gateway_dns]
   account_id = <32-char-hex-account-id>
   access_key_id = <r2-access-key-id>
   secret_access_key = <r2-secret-access-key>
   bucket_name = <bucket-name>
   key_prefix = gateway_dns/
   interval = 300
   index = cloudflare_logs
   sourcetype = cloudflare:dns
   disabled = false
   ```

3. Verify events: `index=cloudflare_logs sourcetype=cloudflare:dns | head 5`

4. Reset checkpoint (re-ingest all files):
   ```bash
   splunk clean inputdata cloudflare_r2
   ```

### Generating test data

```bash
pip install boto3
python3 seed_test_data.py \
  --account-id <account-id> \
  --access-key <access-key-id> \
  --secret-key <secret-access-key> \
  --bucket <bucket-name> \
  --count 3 \
  --records-per-file 25
```

---

## Revision History

| Date | Author | Notes |
|---|---|---|
| 2026-06-07 | Michael Kowal | Initial prototype; end-to-end validated on Splunk 10.4.0 |

---

*This document and the accompanying code are Cloudflare confidential until
legal review and publication approval are obtained.*
