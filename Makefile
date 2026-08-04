TF := terraform -chdir=infra
TF_BOOTSTRAP := terraform -chdir=infra/bootstrap

.PHONY: install lint fmt test test-unit test-integration test-e2e clean \
        tf-init tf-fmt tf-validate tf-plan tf-apply tf-destroy tf-output http-env \
        bootstrap-init bootstrap-plan bootstrap-apply bootstrap-output

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest -m "not e2e"

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

test-e2e:
	uv run pytest tests/e2e -m e2e

clean:
	rm -rf build infra/build .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

# --- terraform -------------------------------------------------------------
# Packaging happens inside `plan` (see infra/layers.tf), so there is no build
# step to run first.

tf-init:
	$(TF) init

tf-fmt:
	$(TF) fmt -recursive

tf-validate:
	$(TF) validate

tf-plan:
	$(TF) plan

tf-apply:
	$(TF) apply

tf-destroy:
	$(TF) destroy

tf-output:
	$(TF) output

# --- bootstrap -------------------------------------------------------------
# One-time setup, run by hand with admin credentials — never from CI, since it
# creates the very roles CI authenticates as. Produces the state bucket that
# `tf-init` above then needs, so it runs before anything else in this file.
#
# Its own state is local and gitignored: infra/bootstrap/terraform.tfstate.
# Losing it is recoverable (four resources to re-import) but annoying.

bootstrap-init:
	$(TF_BOOTSTRAP) init

bootstrap-plan:
	$(TF_BOOTSTRAP) plan

bootstrap-apply:
	$(TF_BOOTSTRAP) apply

bootstrap-output:
	$(TF_BOOTSTRAP) output

# --- http client -----------------------------------------------------------

# Writes the live API URL into the gitignored private env file. The invoke_url
# output carries a trailing slash, which would turn `{{baseUrl}}/items` into a
# double-slashed path, so it is stripped here.
HTTP_ENV_FILE := tests/http/http-client.private.env.json

http-env:
	@url=$$($(TF) output -raw api_base_url); \
	url=$${url%/}; \
	printf '{\n  "dev": {\n    "baseUrl": "%s"\n  }\n}\n' "$$url" > $(HTTP_ENV_FILE); \
	echo "wrote $(HTTP_ENV_FILE) -> $$url"
