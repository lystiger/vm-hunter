---
name: provider-oracle
description: Claim an Oracle Cloud Always Free Ampere A1 VM - signed OCI API requests, the free limits, the cost guard, and how to treat "Out of host capacity". Use for any Oracle Cloud work in this repository.
---

# Oracle Cloud: claiming an Always Free A1 VM

## What is free (the cost guard enforces this)

- Shape `VM.Standard.A1.Flex` only.
- Across the whole tenancy, all A1 instances together: **at most 4 OCPUs and 24 GB memory**.
- Block storage: **at most 200 GB in total** across boot and block volumes. A boot volume is at least
  47 GB on most images; size it explicitly with `bootVolumeSizeInGBs`.
- The tenancy's home region only: Always Free resources must be created in the home region.

### Cost guard (runs before every launch)

1. List existing instances in the compartment and sum the OCPUs and memory of non-terminated
   `VM.Standard.A1.Flex` instances.
2. Refuse the launch if the shape is not `VM.Standard.A1.Flex`, or if existing plus requested would
   exceed 4 OCPUs or 24 GB, or if the requested boot volume would exceed the storage limit.
3. A refusal is a `failure` notification and stops the workflow. It is never retried.

The static checker fails a workflow whose launch request does not come after the cost-guard node.

## Requests

- Base URL: `$env.HUNTER_OCI_API_BASE` (`https://iaas.<region>.oraclecloud.com`), API version `20160918`.
- List: `GET /20160918/instances?compartmentId=<ocid>`.
- Launch: `POST /20160918/instances` with JSON:
  `availabilityDomain`, `compartmentId`, `shape`, `shapeConfig: { ocpus, memoryInGBs }`,
  `sourceDetails: { sourceType: "image", imageId, bootVolumeSizeInGBs }`,
  `createVnicDetails: { subnetId, assignPublicIp: true }`, `metadata: { ssh_authorized_keys }`, `displayName`.

## Signing (open question O1)

OCI uses HTTP signatures (`Authorization: Signature version="1",keyId="<tenancy>/<user>/<fingerprint>",
algorithm="rsa-sha256",headers="...",signature="<base64>"`).
- GET signs `(request-target) date host`.
- POST signs `(request-target) date host x-content-sha256 content-type content-length`, where
  `x-content-sha256` is the base64 SHA-256 of the body.
- The signing string is each header as `name: value` on its own line, in the listed order;
  `(request-target)` is `post /20160918/instances` (lowercase method, path with query).
- Sign with RSA-SHA256 using the PEM key from `$env.HUNTER_OCI_PRIVATE_KEY_PEM`, in a Code node with
  `require('crypto')` (needs `NODE_FUNCTION_ALLOW_BUILTIN=crypto`).
- Test the signer against a known vector in `tests/fixtures/` (generate a throwaway key for the test;
  never a real one). If signing inside n8n proves impossible on the pinned version, stop and report it;
  do not work around the cost guard or the key handling.

## Responses

| Response | Meaning | Action |
|---|---|---|
| 200 with an instance | Claimed | Notify `claim`, stop claiming (record success in static data) |
| 500 `InternalError`, message contains "Out of host capacity" | Normal | Wait and retry |
| 429 `TooManyRequests` | Too fast | Double the wait, up to 15 minutes |
| 400/401/404 | Configuration problem | Notify `failure`, stop: retrying cannot fix it |

Wait at least 60 seconds plus up to 30 seconds of random jitter between attempts.
