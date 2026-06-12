# OpenCode Session Prompt — TA-cloudflare-r2 Maintenance

Paste this entire file into a new OpenCode session opened in the repository root.
The session will have full context to troubleshoot, maintain, and extend the add-on.

---

## What you are working on

You are maintaining the **Cloudflare R2 Log Ingestion Add-on for Splunk**
(`TA-cloudflare-r2`), a Splunk Technology Add-on (TA) that ingests Cloudflare
Logpush log files from Cloudflare R2 via a Python modular input.

**Repository**: https://github.com/kowalified/splunk-ta-cloudflare-r2
*(may have been transferred to Cloudflare's official GitHub org - check current location)*

---

## Why this add-on exists (do not re-derive)

The Splunk Add-on for AWS (Splunkbase 1876) cannot work with Cloudflare R2. It
calls `sts.get_caller_identity()` (AWS STS) before saving credentials AND at
runtime. R2 has no STS service. This is a confirmed dead-end as of v8.1.2 -
no configuration change resolves it, including the Generic S3 input with
`host_name` / `s3_private_endpoint_url`. This add-on replaces the AWS TA for
the R2 use case using boto3 directly with SigV4 + path-style addressing.

---

## Architecture (do not re-derive)

**Core logic is entirely in `TA-cloudflare-r2/bin/cloudflare_r2.py`.**

```
Every <interval> seconds per input instance:
  1. ListObjectsV2(Bucket, Prefix, StartAfter=last_key)
  2. For each new .log.gz key (lexicographically after checkpoint):
     a. GetObject → gzip.decompress → splitlines
     b. Emit each line as one Splunk event
     c. Save checkpoint: {last_key: <this key>} to modinputs dir
```

**R2 boto3 config (non-negotiable, do not change without testing):**
- `endpoint_url = https://<account_id>.r2.cloudflarestorage.com`
- `region_name = "auto"`
- `config = Config(signature_version="s3v4", s3={"addressing_style": "path"})`
- Static access key + secret (no STS, no IAM roles, no instance profiles)

**Checkpoint location:**
`$SPLUNK_HOME/var/lib/splunk/modinputs/cloudflare_r2/<stanza_name>.json`

If `checkpoint_dir` is injected as `/tmp` by splunklib (known SDK quirk), the
code falls back to `$SPLUNK_HOME/var/lib/splunk/modinputs/cloudflare_r2/`
via the `SPLUNK_HOME` env var.

**Key naming / checkpoint ordering:**
Logpush key format: `<prefix>/<YYYYMMDD>/<YYYYMMDDTHHmmSSZ>_<YYYYMMDDTHHmmSSZ>_<random>.log.gz`
Lexicographic sort of timestamp-prefixed keys = chronological order.
`StartAfter=last_key` reliably returns only new files.

**SSL / TLS inspection:**
R2 endpoints on some corporate networks (including Cloudflare's own internal
network) have their TLS cert replaced by a corporate inspection proxy. The
`verify_ssl` input parameter (default `true`) can be set to `false` as a
workaround. The correct fix is adding the inspection CA to the server trust store.

---

## Known gaps (pre-existing, do not treat as bugs)

1. **Credentials in plaintext** - `secret_access_key` stored in `inputs.conf`,
   not in Splunk's `storage/passwords`. Requires a custom REST handler to fix.
   This is documented and intentional for the current version.
2. **Splunk Cloud untested** - only validated on Splunk Enterprise.
3. **UI `disabled` default** - new inputs create as disabled; user clicks Enable.
   The `type="checkbox"` for `disabled` in the manager XML is the current fix.

---

## AppInspect baseline (do not regress)

Running `splunk-appinspect inspect TA-cloudflare-r2-*.tgz --mode precert` must
produce: **0 errors | 0 failures | 0 future_failures**

Known acceptable warnings (do not fix):
- subprocess in vendored dateutil
- Outdated splunk-sdk (update when bumping splunklib)
- admin role not available in Splunk Cloud
- inputs.conf not synced to indexers in Victoria

---

## Local dev environment

```bash
# Start Splunk (linux/amd64 only - runs under Rosetta 2 on Apple Silicon, ~3-5 min boot)
docker run -d --name splunk-dev \
  -p 8000:8000 -p 8089:8089 \
  -e SPLUNK_START_ARGS=--accept-license \
  -e SPLUNK_GENERAL_TERMS=--accept-sgt-current-at-splunk-com \
  -e SPLUNK_PASSWORD=changeme1! \
  -v $(pwd)/TA-cloudflare-r2:/opt/splunk/etc/apps/TA-cloudflare-r2 \
  splunk/splunk:latest

# Check healthy
curl -sk https://localhost:8089/services/server/info -u admin:changeme1! | grep version

# Watch modular input logs (primary debugging tool)
docker exec --user splunk splunk-dev grep "cloudflare_r2" \
  /opt/splunk/var/log/splunk/splunkd.log | tail -30

# Restart Splunk (needed after changes to app.conf or inputs.conf.spec)
curl -sk https://localhost:8089/services/server/control/restart \
  -u admin:changeme1! -X POST
```

**Volume-mounted dev**: code changes in `TA-cloudflare-r2/` take effect on the
next poll interval without any container restart.

---

## Build and release

```bash
# Clean
find TA-cloudflare-r2 -name "*.pyc" -delete 2>/dev/null || true
find TA-cloudflare-r2 -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find TA-cloudflare-r2/metadata -name "local.meta" -delete 2>/dev/null || true
xattr -rc TA-cloudflare-r2/ 2>/dev/null || true

# Build
COPYFILE_DISABLE=1 tar -czf TA-cloudflare-r2-<version>.tgz \
  --exclude="TA-cloudflare-r2/metadata/local.meta" \
  --exclude="*/__pycache__" --exclude="*/.DS_Store" --exclude="*/._*" \
  TA-cloudflare-r2/

# Validate
splunk-appinspect inspect TA-cloudflare-r2-<version>.tgz --mode precert

# Tag and push - do NOT commit the .tgz to git (gitignored)
git tag -a v<version> -m "v<version> - <description>"
git push origin v<version>
# Then create GitHub Release and attach .tgz as release asset
```

---

## Troubleshooting decision tree

**Start here for any "events not appearing" issue:**

```
1. Is the input enabled?
   → Settings > Data Inputs > Cloudflare R2 Log Ingestion
   → If disabled: Enable it

2. Is the input running?
   → docker exec --user splunk splunk-dev grep "cloudflare_r2" splunkd.log | tail -20
   → Look for "starting poll" entries
   → If absent: check app is enabled, restart Splunk

3. What does the poll log say?
   → "poll complete: files=X events=Y" with X>0 → events should be in Splunk, check index/sourcetype
   → "poll complete: files=0 events=0" → checkpoint issue or no new files (see #4)
   → SSL error → set verify_ssl=false or fix CA trust store
   → Auth error (InvalidAccessKeyId, SignatureDoesNotMatch) → regenerate R2 token
   → NoSuchBucket → wrong bucket name or account_id

4. Checkpoint ahead of all R2 files?
   → cat $SPLUNK_HOME/var/lib/splunk/modinputs/cloudflare_r2/<stanza>.json
   → Compare last_key to newest key in R2
   → If checkpoint > newest file: splunk clean inputdata cloudflare_r2

5. R2 reachable at all?
   → Test boto3 connectivity directly (see MAINTENANCE.md Scenario 1 Step 3)
```

---

## Specific maintenance scenarios

### Splunk version upgrade testing

1. Pull new image: `docker pull --platform linux/amd64 splunk/splunk:<new_version>`
2. Start fresh container (no volume mount - test installed package)
3. Install `.tgz` via UI: Apps > Manage Apps > Install app from file
4. Create inputs, verify events appear, restart, verify zero duplicates
5. Run AppInspect
6. Watch for: Python version bump (boto3 compat), new AppInspect checks,
   `python.required` value needing update in `default/inputs.conf`

### Updating vendored dependencies

```bash
# Check current boto3 version
cat TA-cloudflare-r2/bin/lib/botocore/__init__.py | grep __version__

# Clear old boto3 stack
rm -rf TA-cloudflare-r2/bin/lib/{boto3,botocore,s3transfer,jmespath,dateutil,urllib3,six}*

# Reinstall (all pure Python, platform-independent)
pip install boto3 botocore s3transfer jmespath python-dateutil urllib3 six \
  --target TA-cloudflare-r2/bin/lib --upgrade

# Prune botocore/data to S3 + STS only (keeps package small)
cd TA-cloudflare-r2/bin/lib/botocore/data
for item in */; do
  case "${item%/}" in s3|sts) ;; *) rm -rf "$item" ;; esac
done
cd -

# Rebuild and re-run AppInspect
```

### Cloudflare R2 API changes

The add-on uses only `ListObjectsV2` and `GetObject`. If R2 changes these:
- Check https://developers.cloudflare.com/r2/api/s3/api/ for breaking changes
- Test with the seed script: `python3 seed_test_data.py --help`
- The boto3 config in `_make_r2_client()` (line ~75 of cloudflare_r2.py) is the
  only place R2-specific config lives

### Logpush key format changes

The checkpoint relies on lexicographic sort. Current format:
`<prefix>/<YYYYMMDD>/<START>_<END>_<RANDOM>.log.gz`

If Cloudflare changes this format, verify new keys still sort chronologically.
Check real files: `wrangler r2 object list <bucket>` or via boto3 ListObjectsV2.

### New AppInspect failure in vendored library

If a new AppInspect check fires on a vendored library file (not our code):
1. Identify the specific file and line from the AppInspect output
2. If it's a dead code path never triggered by our usage (like the CSM UDP socket
   was in botocore/session.py), make the minimal surgical change to satisfy
   AppInspect (set the function to `return None` or remove the dead import)
3. Document the change with a comment explaining it's an AppInspect compliance fix

---

## File map (what each file does)

```
TA-cloudflare-r2/bin/cloudflare_r2.py      ← ALL core logic here, read this first
TA-cloudflare-r2/bin/lib/                  ← vendored deps (boto3, splunklib, urllib3)
TA-cloudflare-r2/default/app.conf          ← app metadata, version, triggers
TA-cloudflare-r2/default/inputs.conf       ← default stanza (disabled=false, interval=300)
TA-cloudflare-r2/default/data/ui/manager/cloudflare_r2.xml  ← Splunk UI form
TA-cloudflare-r2/README/inputs.conf.spec   ← parameter definitions (required by Splunk)
TA-cloudflare-r2/metadata/default.meta     ← access control
TA-cloudflare-r2/LICENSE                   ← Apache 2.0
seed_test_data.py                          ← dev utility: upload synthetic test data to R2
```

---

## Useful commands reference

```bash
# Search Splunk for events
curl -sk https://localhost:8089/services/search/jobs \
  -u admin:changeme1! \
  -d "search=search index=cloudflare_logs | stats count by sourcetype" \
  -d output_mode=json -d exec_mode=oneshot -d earliest_time=-1h \
  | python3 -c "import sys,json; [print(r) for r in json.load(sys.stdin)['results']]"

# Read checkpoint
docker exec --user splunk splunk-dev \
  cat /opt/splunk/var/lib/splunk/modinputs/cloudflare_r2/<stanza>.json

# Reset all checkpoints
docker exec --user splunk splunk-dev \
  /opt/splunk/bin/splunk clean inputdata cloudflare_r2

# List R2 objects (verify Logpush is writing)
python3 -W ignore -c "
import boto3; from botocore.config import Config
c = boto3.client('s3',
  endpoint_url='https://<ACCOUNT_ID>.r2.cloudflarestorage.com',
  aws_access_key_id='<KEY>', aws_secret_access_key='<SECRET>',
  region_name='auto', config=Config(s3={'addressing_style':'path'}))
r = c.list_objects_v2(Bucket='<BUCKET>', MaxKeys=5)
[print(o['Key']) for o in r.get('Contents',[])]
"

# Test --scheme (verify add-on loads correctly)
docker exec --user splunk splunk-dev /bin/bash -c \
  "LD_LIBRARY_PATH=/opt/splunk/lib /opt/splunk/bin/python3 \
   /opt/splunk/etc/apps/TA-cloudflare-r2/bin/cloudflare_r2.py --scheme 2>&1" \
  | grep -o "<title>.*</title>"
```

---

## Reference links

- R2 S3 API: https://developers.cloudflare.com/r2/api/s3/api/
- Logpush: https://developers.cloudflare.com/logs/logpush/
- Logpush datasets/fields: https://developers.cloudflare.com/logs/logpush/logpush-job/datasets/
- Splunk modular input dev: https://dev.splunk.com/enterprise/docs/developapps/manageknowledge/custominputs/
- AppInspect: https://dev.splunk.com/enterprise/docs/releaseapps/appinspect/
- splunk/splunk Docker: https://hub.docker.com/r/splunk/splunk
