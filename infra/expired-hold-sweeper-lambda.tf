module "expired_hold_sweeper" {
  source = "./modules/lambda_function"

  name_prefix  = local.name_prefix
  service_name = "expired_hold_sweeper"
  source_dir   = "${local.src_dir}/src/lambdas/expired_hold_sweeper"

  runtime      = var.python_runtime
  architecture = var.lambda_architecture

  log_retention_days = var.log_retention_days

  use_shared_layer = true
  shared_layer_arn = aws_lambda_layer_version.shared.arn

  kms_key_arn = aws_kms_key.payledger_key.arn

  enable_active_tracing = true

  environment_variables = {
    LEDGER_TABLE_NAME       = aws_dynamodb_table.ledger.name
    LOG_LEVEL               = "INFO"
    POWERTOOLS_SERVICE_NAME = "expired_hold_sweeper"
  }
}
