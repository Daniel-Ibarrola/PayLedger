locals {
  authorization_service_name = "${local.name_prefix}-authorization-service"
  authorization_service_dir  = "${local.src_dir}/src/lambdas/authorization_service"
}

# Top-level *.py only, which is also what excludes __pycache__ and
# requirements.txt without needing an exclude list.
data "archive_file" "authorization_service" {
  type        = "zip"
  output_path = "${path.module}/build/authorization-service.zip"

  dynamic "source" {
    for_each = fileset(local.authorization_service_dir, "*.py")

    content {
      content  = file("${local.authorization_service_dir}/${source.value}")
      filename = source.value
    }
  }
}

# Created explicitly rather than left to Lambda: a group the function creates on
# first invocation has no retention policy and keeps logs forever, and Terraform
# never learns it exists so `destroy` leaves it behind.
resource "aws_cloudwatch_log_group" "authorization_service" {
  name              = "/aws/lambda/${local.authorization_service_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "authorization_service" {
  function_name = local.authorization_service_name
  role          = aws_iam_role.authorization_service.arn
  handler       = "handler.lambda_handler"
  runtime       = var.python_runtime
  architectures = [var.lambda_architecture]

  filename         = data.archive_file.authorization_service.output_path
  source_code_hash = data.archive_file.authorization_service.output_base64sha256

  layers = [aws_lambda_layer_version.shared.arn]

  memory_size = 512
  timeout     = 10

  environment {
    variables = {
      LEDGER_TABLE_NAME = aws_dynamodb_table.ledger.name
      LOG_LEVEL         = "INFO"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.authorization_service,
    aws_iam_role_policy_attachment.authorization_service_basic_execution,
  ]
}
