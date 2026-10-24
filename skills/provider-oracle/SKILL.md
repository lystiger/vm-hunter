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
2. List existing boot volumes and block volumes in the compartment and sum `sizeInGBs` of every one
   whose `lifecycleState` is not `TERMINATED` or `TERMINATING`.
3. Refuse the launch if the shape is not `VM.Standard.A1.Flex`, or if existing plus requested would
   exceed 4 OCPUs or 24 GB, or if existing storage plus the requested boot volume would exceed 200 GB.
   Refuse too when any list request fails: an unknown total is not a free total.
4. A refusal is a `failure` notification and stops the workflow. It is never retried.

The static checker fails a workflow whose launch request does not come after the cost-guard node.

## Requests

- Base URL: `$env.HUNTER_OCI_API_BASE` (`https://iaas.<region>.oraclecloud.com`), API version `20160918`.
- List instances: `GET /20160918/instances?compartmentId=<ocid>`.
- List boot volumes: `GET /20160918/bootVolumes?compartmentId=<ocid>&availabilityDomain=<ad>`.
- List block volumes: `GET /20160918/volumes?compartmentId=<ocid>`.
- List responses are JSON arrays. When the `opc-next-page` response header is present, request again
  with `&page=<that value>` until it is absent; the guard must count every page.
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
- Oracle's docs publish example signing strings but no expected signature. RSA-SHA256 (PKCS#1 v1.5)
  is deterministic, so test the signer against an independent implementation: build the signing string
  with the Code node, then check its signature equals `openssl dgst -sha256 -sign <key>` over the same
  string, base64-encoded. Use a throwaway key generated for the test (`openssl genrsa`), never a real one.
  Also check `x-content-sha256` equals `openssl dgst -sha256 -binary | base64` of the body. If signing inside n8n proves impossible on the pinned version, stop and report it;
  do not work around the cost guard or the key handling.

## Responses

| Response | Meaning | Action |
|---|---|---|
| 200 with an instance | Claimed | Notify `claim`, stop claiming (record success in static data) |
| 500 `InternalError`, message contains "Out of host capacity" | Normal | Wait and retry |
| 429 `TooManyRequests` | Too fast | Double the wait, up to 15 minutes |
| 400/401/404 | Configuration problem | Notify `failure`, stop: retrying cannot fix it |

Wait at least 60 seconds plus up to 30 seconds of random jitter between attempts.

## Recorded response shapes

Agents have no network access, so do not guess these; they come from the OCI API reference. Build
fixtures from them with fake OCIDs (`ocid1.instance.oc1..example`).

- Error responses are JSON `{"code": "...", "message": "..."}` with an `opc-request-id` header.
  - Out of capacity: HTTP 500, `{"code": "InternalError", "message": "Out of host capacity."}`
  - Throttled: HTTP 429, `{"code": "TooManyRequests", "message": "Too many requests for the user"}`
  - Bad auth: HTTP 401, `{"code": "NotAuthenticated", "message": "The required information to complete authentication was not provided or was incorrect."}`
  - Bad request: HTTP 400, `{"code": "InvalidParameter", "message": "..."}`; missing resource: HTTP 404, `{"code": "NotAuthorizedOrNotFound", "message": "..."}`
- A launched or listed instance: `{"id", "displayName", "compartmentId", "availabilityDomain",
  "shape": "VM.Standard.A1.Flex", "shapeConfig": {"ocpus": 4.0, "memoryInGBs": 24.0},
  "lifecycleState": "PROVISIONING", "timeCreated": "2026-10-24T08:00:00.000Z"}`. `lifecycleState` is one
  of `MOVING, PROVISIONING, RUNNING, STARTING, STOPPING, STOPPED, CREATING_IMAGE, TERMINATING, TERMINATED`.
- A boot volume or block volume: `{"id", "availabilityDomain", "compartmentId", "sizeInGBs": 47,
  "lifecycleState": "AVAILABLE"}`; states include `PROVISIONING, RESTORING, AVAILABLE, TERMINATING,
  TERMINATED, FAULTY`.
