# Fargate renderer feasibility probe (Phase 7-B)

This is a disposable infrastructure probe, not a production renderer or a qualification issuer.
The API, existing saved renderer reports, templates and OCR jobs do not call this code.
See [migration design](../../docs/web-beta-fargate-plan-2026-10-16.md).

AWS profile: **abe-deploy-user**, account **535562976288**, region **ap-northeast-1**.
Infrastructure lives in `terraform/aws/renderer_probe`, with its own remote state key
`probe/renderer/terraform.tfstate`. It reads the existing VPC/subnet without taking ownership.
The cluster has no service, no recurring task and no inbound security-group rule.

The fixed task has two separate container PID namespaces and no task IAM role:

1. Transport downloads one bounded input through a short-lived, exact-object HTTPS URL.
   The S3 signing client must explicitly use `https://s3.ap-northeast-1.amazonaws.com`.
   The tested SDK default endpoint produced signature errors. URLs go only to the transport
   container's runtime overrides, never to Terraform, native environment, shared files or logs.
2. Native runs as UID/GID 10001, with a read-only root and input, zero capabilities,
   no-new-privileges, and a seccomp filter installed before parsing the input.
   Only AF_UNIX sockets are allowed for Office IPC. Internet/metadata socket families,
   io_uring, ptrace/process-memory access and namespace escape operations are denied.
   Exec children inherit the guard. No unguarded rendering fallback exists.
3. Native's hard RLIMITs bound CPU time, process count, file size and core dumps.
   Its ECS container memory limit is 2 GiB; the task is 1 vCPU / 3 GiB.
   Native and transport have independent wall-time alarms. Transport is essential,
   so its exit stops the task even if native fails. The operator also bounds task lifetime
   and stops only task ARNs returned by its own launch calls.
4. Transport packages bounded outputs into one exact-object upload. The external verifier
   checks the input SHA, file SHA and ECS image digest, exit state and task definition.
   Failures never produce evidence accepted by `linux-container-renderer-v1`.

Build requires the frozen Phase 7 parent images, checked **before** invoking Docker:

| Parent | Required local image ID |
| --- | --- |
| Renderer | `sha256:55409c022c64f4d74bf60ab4b17dc701ceef11cfc8db7b3a617f914d59364141` |
| Backend (public CA bundle only) | `sha256:20f073b08915826a753b8ec3f6d27643f3e6cf2aa8f0fdd0dec96eeabd48f674` |

Do not use a whole checkout as the build context. Copy only this Dockerfile and two scripts
to an isolated build context; no `.env`, DB, credential file or normal storage is required.
Terraform accepts only an ECR digest, not a tag. ECR tags are immutable.

Remaining production work includes the API launch/cancel/restart boundary, strict streaming
output validation and storage quotas, actual task-definition admission, durable job ownership,
new environment evidence and candidate policy, dependency/font identity and regression.
An RLIMIT process count is different from Docker's old cgroup pids limit. The task's 20 GiB
ephemeral storage is different from the old 64 MiB output tmpfs. These differences must be
explicitly specified and validated before connecting the renderer to the product.

Evidence and executable acceptance runners are outside the repository at
`D:\llm-ocr-archive\beta\20261016-phase7b`. Signed URLs and AWS credentials are excluded
from reports. Probe S3 objects expire after one day; logs after seven days. Task compute
must be stopped at phase completion, including failure cases. Existing EC2 remains stopped.
