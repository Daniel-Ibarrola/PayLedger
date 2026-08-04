# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PayLedger — a card authorization and double-entry ledger service on AWS serverless, built as a 3-week
learning project. **`docs/design-doc.md` is the source of truth** for the data model, API shape, invariants,
ADRs, cost model, and security posture; `docs/roadmap.json` sequences the work against it. Where any other
document disagrees with the design doc, the design doc wins.

**Current state: skeleton only.** The single deployed service is `authorization_service`, a deliberately trivial
toy CRUD pair over a one-key placeholder table. It exists to prove the path API Gateway → shared layer →
boto3 → DynamoDB, and is meant to be deleted rather than migrated. None of the ledger domain (accounts,
authorizations, ledger entries, idempotency, saga, Aurora projector) exists yet.

## Commands

```bash
make install            # uv sync
make lint               # ruff check + ruff format --check
make fmt                # ruff format + ruff check --fix
make test               # everything except e2e (this is what CI runs)
make test-unit          # tests/unit only, no Docker needed
make test-integration   # tests/integration — needs Docker (DynamoDB Local via testcontainers)
make test-e2e           # tests/e2e — runs against deployed AWS resources
```

Single test / single file:

```bash
uv run pytest tests/unit/test_shared_utils.py::TestRouteKey -v
uv run pytest tests/integration/test_items_handler.py -k missing_item
```

Terraform (all wrap `terraform -chdir=infra`): `make tf-init tf-fmt tf-validate tf-plan tf-apply tf-destroy
tf-output`. Packaging happens inside `plan` (see below), so there is no build step to run first.

`make http-env` writes the live API URL from Terraform outputs into the gitignored
`tests/http/http-client.private.env.json`, used by PyCharm's HTTP Client (`tests/http/items_service.http`).

## Architecture

### Python layout mirrors the Lambda runtime

- `src/layers/shared/` is packaged as a Lambda layer and unpacked to `/opt/python/shared`, so handlers
  import it as `from shared import utils`. `pyproject.toml`'s `pythonpath = ["src/layers", "src/lambdas"]`
  reproduces that import path locally — that is why there is no installed package and no `src/` prefix in
  imports.
- `src/lambdas/<service>/handler.py` exposes `lambda_handler` plus a `ROUTES` dict keyed on API Gateway
  **HTTP API payload format 2.0** route keys (`"POST /items"`, `"GET /items/{item_id}"`).

### The route-key contract

`ROUTES` keys must match the `route_key` values in `infra/apigateway.tf` exactly. If they drift, API Gateway
accepts the request and the Lambda 404s from the inside. `utils.route_key()` dispatches on the path
*template*, not the resolved path.

### Error handling

Handlers raise `ApiError` subclasses (`BadRequest`/`NotFound`/`Conflict` in `shared/errors.py`); the
top-level `lambda_handler` maps them to responses via `utils.error_response`. Unexpected exceptions are
logged and returned as a bare 500 — exception text never reaches the caller.

### Money

Integer minor units (cents) everywhere; no floats. `utils._json_default` exists because DynamoDB's resource
API returns every number as `Decimal` — integral values must serialize back to `int`.

### Terraform packaging has no build step

`infra/layers.tf` and `infra/lambda.tf` build their zips with `archive_file` `source` blocks that place each
`.py` file at its target path directly (`python/shared/...` for the layer), so packaging happens during
`terraform plan`. **This only works while the layer is pure first-party Python.** The moment
`src/layers/shared/requirements.txt` gains a real entry, this must become a genuine build step
(`pip install -t` in a manylinux container matching `var.lambda_architecture`).

### Two Terraform roots

- `infra/` — the deployed stack. S3 remote backend with native lockfile locking (`use_lockfile`); applied by
  CI on merge to main.
- `infra/bootstrap/` — applied by hand once, with admin credentials, **state stays local** (it creates the
  bucket that holds the other root's state). Owns the state bucket and the two GitHub OIDC roles.

### CI/CD

- `.github/workflows/ci.yml` — ruff lint/format, then `pytest -m "not e2e"`. It pre-pulls the DynamoDB Local
  image (read from `tests.conftest.DYNAMODB_LOCAL_IMAGE`) because testcontainers counts pull time against its
  start timeout. No AWS credentials; this job must never need any.
- `.github/workflows/deploy.yml` — `terraform plan` on PR under a read-only role, `terraform apply` on push
  to main under a write role. The split is enforced by `sub` conditions in the roles' trust policies
  (`infra/bootstrap/oidc.tf`), not by the workflow's `if:` conditions.

## Conventions to preserve

These come from the design doc and are already visible in the existing Terraform — new resources are
expected to follow them:

- **One IAM role per function**, no shared role. Policies name concrete resource ARNs, never `*`; grant
  exactly the API calls the handler makes. Where a handler queries a GSI, the index ARN must be named
  separately or every index query silently fails.
- **Log groups are created explicitly in Terraform** with `retention_in_days`. A group Lambda creates on
  first invocation retains forever and survives `destroy`.
- **Append-only ledger.** Corrections are new opposite-sign entries under a new transaction id; posted
  entries are never updated or deleted (ADR-5), and this is enforced in IAM as well as in code.
- **`account_id` comes from the validated Cognito `sub`**, never from a request body or path — the API shape
  (`/accounts/me/...`, no `account_id` field) is the enforcement mechanism.
- **Cost guardrails** (the project targets $10–30 total): DynamoDB on-demand, no NAT Gateway, short log
  retention, Aurora created only in week 2 with min 0 ACU.
- Terraform comments explain *why* a non-obvious choice was made; that density is the house style in both
  the `.tf` files and the Python.

Commit messages use a lowercase `type: summary` prefix (`setup:`, `ci:`, `refine design doc:`).

## Testing notes

- `tests/conftest.py` disables testcontainers' Ryuk reaper (it cannot start against the Docker socket on this
  setup) and stops every container explicitly in a `finally`. It also stubs fake AWS credentials session-wide
  so boto3 cannot reach a real account.
- `shared/dynamo.py` honours `DYNAMODB_ENDPOINT_URL`, set only by the test harness; in Lambda it is absent and
  boto3 resolves the real regional endpoint. The boto3 resource is cached keyed on endpoint+region rather than
  in a module global, so tests can point at a container without pinning it for the session.
- Integration tests build API Gateway v2 events via `tests/integration/fixtures/events.py` and call
  `lambda_handler` directly — they test the wiring, not domain logic.
