locals {
  function_name = "${var.name_prefix}-${var.service_name}"
  layers        = var.use_shared_layer ? [var.shared_layer_arn] : []
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = local.function_name
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
}

# Managed policy only for the CloudWatch Logs plumbing. Everything the function
# actually touches gets an inline, resource-scoped policy attached by the
# caller - the design doc's IAM section calls for one role per function with
# no shared blanket policy.
resource "aws_iam_role_policy_attachment" "basic_execution" {
  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "xray" {
  count = var.enable_active_tracing ? 1 : 0

  role       = aws_iam_role.this.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# Top-level *.py only, which is also what excludes __pycache__ and
# requirements.txt without needing an exclude list.
data "archive_file" "this" {
  type        = "zip"
  output_path = "${path.module}/../../build/${var.service_name}.zip"

  dynamic "source" {
    for_each = fileset(var.source_dir, "*.py")

    content {
      content  = file("${var.source_dir}/${source.value}")
      filename = source.value
    }
  }
}

# Created explicitly rather than left to Lambda: a group the function creates
# on first invocation has no retention policy and keeps logs forever, and
# Terraform never learns it exists so `destroy` leaves it behind.
resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${local.function_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

resource "aws_lambda_function" "this" {
  function_name = local.function_name
  role          = aws_iam_role.this.arn
  handler       = var.handler
  runtime       = var.runtime
  architectures = [var.architecture]

  filename         = data.archive_file.this.output_path
  source_code_hash = data.archive_file.this.output_base64sha256

  layers = local.layers

  memory_size = var.memory_size
  timeout     = var.timeout

  environment {
    variables = var.environment_variables
  }

  tracing_config {
    mode = var.enable_active_tracing ? "Active" : "PassThrough"
  }

  depends_on = [
    aws_cloudwatch_log_group.this,
    aws_iam_role_policy_attachment.basic_execution,
    aws_iam_role_policy_attachment.xray,
  ]
}
