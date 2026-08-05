module "create_account" {
  source = "./modules/lambda_function"

  name_prefix  = local.name_prefix
  service_name = "create-account"
  source_dir   = "${local.src_dir}/src/lambdas/create_account"

  runtime      = var.python_runtime
  architecture = var.lambda_architecture

  log_retention_days = var.log_retention_days

  use_shared_layer = true
  shared_layer_arn = aws_lambda_layer_version.shared.arn

  environment_variables = {
    LEDGER_TABLE_NAME = aws_dynamodb_table.ledger.name
    LOG_LEVEL         = "INFO"
  }
}
