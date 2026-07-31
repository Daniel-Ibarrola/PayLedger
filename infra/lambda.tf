locals {
  items_service_name = "${local.name_prefix}-items-service"
  items_service_dir  = "${local.src_dir}/src/lambdas/items_service"
}

# Top-level *.py only, which is also what excludes __pycache__ and
# requirements.txt without needing an exclude list.
data "archive_file" "items_service" {
  type        = "zip"
  output_path = "${path.module}/build/items-service.zip"

  dynamic "source" {
    for_each = fileset(local.items_service_dir, "*.py")

    content {
      content  = file("${local.items_service_dir}/${source.value}")
      filename = source.value
    }
  }
}

# Created explicitly rather than left to Lambda: a group the function creates on
# first invocation has no retention policy and keeps logs forever, and Terraform
# never learns it exists so `destroy` leaves it behind.
resource "aws_cloudwatch_log_group" "items_service" {
  name              = "/aws/lambda/${local.items_service_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "items_service" {
  function_name = local.items_service_name
  role          = aws_iam_role.items_service.arn
  handler       = "handler.lambda_handler"
  runtime       = var.python_runtime
  architectures = [var.lambda_architecture]

  filename         = data.archive_file.items_service.output_path
  source_code_hash = data.archive_file.items_service.output_base64sha256

  layers = [aws_lambda_layer_version.shared.arn]

  # 512 MB is well past what this handler needs, but Lambda scales CPU with
  # memory, so a larger size often costs less per request than the floor does.
  # Revisit with real numbers during the week 3 load test.
  memory_size = 512
  timeout     = 10

  environment {
    variables = {
      TOY_TABLE_NAME = aws_dynamodb_table.toy_items.name
      LOG_LEVEL      = "INFO"
      # DYNAMODB_ENDPOINT_URL is deliberately unset — the test harness is the
      # only thing that sets it, and in Lambda boto3 must resolve the real
      # regional endpoint.
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.items_service,
    aws_iam_role_policy_attachment.items_service_basic_execution,
  ]
}
