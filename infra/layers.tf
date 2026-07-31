# The shared layer.
#
# A layer's contents are unpacked to /opt, and /opt/python is on the Lambda
# runtime's sys.path — so the package has to sit at `python/shared/` inside the
# zip, not at `shared/`. Rather than staging that directory on disk with a build
# script, each file is placed at its target path directly via a `source` block,
# which keeps the packaging step inside `terraform plan` and out of a Makefile
# the plan cannot see.
#
# This works because the layer is pure first-party Python. The moment
# src/layers/shared/requirements.txt gains an entry, this has to become a real
# build step (pip install -t, in a manylinux container matching the target
# architecture) — a `source` block cannot install a wheel.

locals {
  shared_layer_dir   = "${local.src_dir}/src/layers/shared"
  shared_layer_files = fileset(local.shared_layer_dir, "**/*.py")
}

data "archive_file" "shared_layer" {
  type        = "zip"
  output_path = "${path.module}/build/shared-layer.zip"

  dynamic "source" {
    for_each = local.shared_layer_files

    content {
      content  = file("${local.shared_layer_dir}/${source.value}")
      filename = "python/shared/${source.value}"
    }
  }
}

resource "aws_lambda_layer_version" "shared" {
  layer_name = "${local.name_prefix}-shared"
  filename   = data.archive_file.shared_layer.output_path
  # Forces a new layer version whenever the packaged bytes change; without it
  # Terraform sees no diff and the functions keep the stale version.
  source_code_hash = data.archive_file.shared_layer.output_base64sha256

  compatible_runtimes      = [var.python_runtime]
  compatible_architectures = [var.lambda_architecture]

  description = "Shared payledger helpers (utils, errors, dynamo)."
}
