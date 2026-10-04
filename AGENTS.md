# Contributor and AI-agent instructions

This repository documents the future internal generation-domain controller for FLAMORIS. It is not implemented yet. Read README.md, docs/ARCHITECTURE.md, docs/MIGRATION.md and [FLAMORIS AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) before proposing changes.

## Current authorization

The current correction stage permits documentation and Issue organization in Chat only. Terminology is fixed: `ComfyWorkFlow` for ComfyUI execution definitions and `ExecutionPlan` for AI Runtime inference execution. Avoid bare `Workflow` for either. It does not authorize implementation, Work delegation, merging, deployment, runtime restart, live generation, credential changes or further Workflow/reference-image feature development. Merging documentation does not automatically lift this hold. Wait for an explicit user instruction before implementation or migration.

## Non-negotiable boundaries

1. Generation MCP is an external MCP adapter; the Controller is internal generation logic. Internal application/service calls do not use MCP or MCP Hub.
2. ComfyWorkFlow means provider execution-definition construction, particularly ComfyUI API-format JSON. ExecutionPlan means inference control. Never transfer the ComfyUI builder to AI Runtime because both use the word Workflow.
3. ComfyUI executes ComfyUI graphs. The Controller builds/validates/submits them and tracks generation-domain results; it does not implement a replacement ComfyUI or inference scheduler.
4. Agent is optional personality/conversation/memory behavior, not a prerequisite for ordinary generation or JSON construction.
5. GPU Node Manager retains host lifecycle authority. Readiness evidence is not permission to copy host/systemd control into this repository.
6. Keep one generation job/input/asset authority shared by internal and MCP callers. Two frontends must not create competing registries, reservations or stores.
7. Reuse existing evidence and tests deliberately, but do not migrate the current Generation MCP ComfyWorkFlow implementation. Later cleanup should delete that misplaced implementation from the MCP repository; future Controller code is a separate design/implementation decision.

## Implementation reality versus target

This repository currently contains documentation, not migrated runtime code. Current source in each owning repository determines what exists; the explicit #18 decision determines the target boundary. Do not treat old internal-MCP wiring as permission to extend it. Do not describe planned capabilities as implemented.

Before later extraction, inspect current main and exact source/test paths in Generation MCP and the actual Studio consumers. Record source revision, destination, compatibility impact and preserved behavior. Do not invent APIs, ports, files, schema migrations, state names or runtime commands.

## Safety and compatibility

Preserve authorization, immutable input/reference identity, bounded decoding/staging/transfer, path confinement, error redaction and no replay after uncertain submission. Preserve existing accepted verification/readiness protections until a separately reviewed change replaces them. Distinguish static JSON validation, provider availability and real execution qualification.

Keep provider credentials in operator-controlled configuration, never in browser data, personality, prompts, public errors or repository files. No arbitrary shell, caller paths/URLs, hidden model downloads, automatic fallback or implicit GPU activation.

## Review and tests

Use small documentation PRs now. Later code changes need separate approval and focused tests. Builder tests should run offline with fixtures; provider contract tests and live acceptance are distinct. Do not claim runtime tests, CI success, deployment or model readiness without actual evidence.

Read the [shared repository policy](https://github.com/flamoris-jp/flamoris-commons/blob/main/docs/repository-policy.md). Keep public docs portable and third-party/model licenses explicit. Do not force-push shared branches or auto-merge without permission.
