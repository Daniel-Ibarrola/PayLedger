# The shared layer.
#
# A layer's contents are unpacked to /opt, and /opt/python is on the Lambda
# runtime's sys.path — so the package has to sit at `python/shared/` inside the
# zip, not at `shared/`.
#
# Now that src/layers/shared/requirements.txt has real entries (Powertools,
# Pydantic), a `source` block listing first-party files can no longer produce
# the layer on its own — third-party deps must be installed too, and pydantic
# ships a compiled extension that has to match var.lambda_architecture.
# data.external below shells out to a build script that installs those deps
# (as prebuilt wheels for the target architecture, no compiler needed) and
# copies in shared/*.py, staging both under one directory that archive_file
# then zips. `external` data sources are evaluated on every plan, same as the
# old pure-archive_file approach, so packaging still happens inside
# `terraform plan` with nothing to run first.

locals {
  shared_layer_dir       = "${local.src_dir}/src/layers/shared"
  shared_layer_build_dir = "${path.module}/build/shared-layer-python"
  # var.python_runtime is "python3.13"; pip's --python-version wants "3.13".
  python_version = replace(var.python_runtime, "python", "")
}

data "external" "shared_layer_build" {
  program = ["python3", "${path.module}/scripts/build_shared_layer.py"]

  query = {
    shared_dir     = local.shared_layer_dir
    requirements   = "${local.shared_layer_dir}/requirements.txt"
    output_dir     = local.shared_layer_build_dir
    architecture   = var.lambda_architecture
    python_version = local.python_version
  }
}

data "archive_file" "shared_layer" {
  type        = "zip"
  source_dir  = data.external.shared_layer_build.result.output_dir
  output_path = "${path.module}/build/shared-layer.zip"
}

resource "aws_lambda_layer_version" "shared" {
  layer_name = "${local.name_prefix}-shared"
  filename   = data.archive_file.shared_layer.output_path
  # Forces a new layer version whenever the packaged bytes change; without it
  # Terraform sees no diff and the functions keep the stale version.
  source_code_hash = data.archive_file.shared_layer.output_base64sha256

  compatible_runtimes      = [var.python_runtime]
  compatible_architectures = [var.lambda_architecture]

  description = "Shared payledger helpers (utils, errors, dynamo, Powertools, Pydantic)."
}
