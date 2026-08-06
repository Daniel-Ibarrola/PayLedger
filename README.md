# PayLedger

[![CI](https://github.com/Daniel-Ibarrola/PayLedger/actions/workflows/ci.yml/badge.svg)](https://github.com/Daniel-Ibarrola/PayLedger/actions/workflows/ci.yml)

A card authorization and double-entry ledger service on AWS serverless: place a hold, capture or void it,
read a balance, page through history. Built as a three-week ramp-up project to work through the patterns the
domain forces on you — idempotency, transactional integrity without a transaction manager, saga compensation,
CQRS, and cost discipline.


Current status, architecture, cost model, and API shape live in [`docs/design/`](docs/design/README.md) (the
source of truth) and [`docs/roadmap.json`](docs/roadmap.json) (sequencing); this README doesn't duplicate
them, since duplicated copies drift out of sync with the real thing.

## Layout

```
src/layers/shared/     packaged as a Lambda layer; unpacked to /opt/python/shared
src/lambdas/<svc>/     one handler per service; ROUTES dict keyed on API Gateway route keys
infra/                 the deployed stack (S3 remote state, applied by CI)
infra/bootstrap/       one-off: state bucket + GitHub OIDC roles (local state, applied by hand)
tests/unit             no Docker, no AWS
tests/integration      DynamoDB Local via testcontainers
tests/e2e              runs against deployed resources
tests/http/            PyCharm HTTP Client requests against the live API
docs/                  design doc, runbook, roadmap
```

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Python 3.13, Docker (for integration tests), Terraform ≥ 1.9,
and AWS credentials if you intend to deploy.

```bash
make install     # uv sync
make test        # unit + integration (starts DynamoDB Local in a container)
make lint        # ruff check + format --check
```

Run a single test the usual way:

```bash
uv run pytest tests/unit/test_shared_utils.py::TestRouteKey -v
```

## Deploying

The stack deploys itself: `terraform apply` on merge to `main` under a GitHub OIDC role, with a read-only
`terraform plan` posted as a comment on every PR. Nothing needs to be deployed by hand.

To deploy from a workstation instead, the state bucket and CI roles have to exist first. That is a one-time,
admin-credentials step:

```bash
terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap apply    # state bucket + gha-payledger-{plan,apply} roles
```

Then, from the repo root:

```bash
make tf-init
make tf-apply
make tf-output      # api_base_url, table name, function name, log group
make http-env       # writes the API URL into the gitignored HTTP Client env file
```

There is no build step — the Lambda and layer zips are assembled inside `terraform plan` from `archive_file`
`source` blocks. That holds only while the shared layer stays pure first-party Python; the first third-party
dependency in `src/layers/shared/requirements.txt` turns it into a real `pip install -t` build against
manylinux/arm64.

Region defaults to `us-east-2`, Lambda to Python 3.13 on `arm64`. Everything tunable is in
`infra/variables.tf`; copy `infra/terraform.tfvars.example` to `terraform.tfvars` to override.

Set a budget alarm before deploying anything, and `make tf-destroy` when you are not actively testing — the
cost model and targets are in [`docs/design/07-cost-model.md`](docs/design/07-cost-model.md).

The API deployed today is **unauthenticated** until Cognito lands, which is why the API URL is kept out of
git — see [`tests/http/README.md`](tests/http/README.md). Routes, planned and current, are documented in
`docs/design/`.

## The invariants

The system is correct if these hold under concurrency and adversarial input:

1. For every transaction, the sum of all ledger entries equals zero.
2. `available_balance = current_balance - sum(active_holds)`, at all times.
3. An authorization can be captured at most once, for exactly its authorized amount.
4. Replaying a request with the same idempotency key returns the original response, never a second effect.
5. Expired holds (default 7 days) release automatically.

Posted ledger entries are never mutated or deleted; corrections are new opposite-sign entries under a new
transaction id. That rule is enforced in IAM as well as in code — every role except `CompensateLedger` carries
an explicit `Deny` on `dynamodb:DeleteItem`.

## Docs

- [`docs/design/`](docs/design/README.md) — **the source of truth.** Data models, single-table design,
  ADRs, cost model, security, and an explicit list of the sections still missing. Where any other document
  disagrees with it, it wins.
- [`docs/runbook.md`](docs/runbook.md) — what to do at 3am when the ledger is out of balance, the DLQ is
  filling, the projection is lagging, Aurora is at max ACU, or RDS Proxy is out of connections.
- [`docs/roadmap.json`](docs/roadmap.json) — every task across the three weeks with its current status,
  dependencies, and the invariant it protects.
