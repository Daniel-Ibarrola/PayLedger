resource "aws_cognito_user_pool" "main" {
  name = "${local.name_prefix}-user-pool"

  # Auto-verified email attributes
  auto_verified_attributes = ["email"]

  schema {
    name                = "email"
    attribute_data_type = "String"
    mutable             = true
    required            = true
  }

  schema {
    name                = "name"
    attribute_data_type = "String"
    mutable             = true
  }

  lambda_config {
    post_confirmation = module.create_account.function_arn
  }

  # Cognito schema attributes are immutable after pool creation — any diff here
  # is provider-representation drift, not a real change, and AWS rejects the
  # UpdateUserPool call outright ("cannot modify or remove schema items").
  lifecycle {
    ignore_changes = [schema]
  }
}

resource "aws_cognito_user_pool_client" "main" {
  name                = "${local.name_prefix}-client"
  user_pool_id        = aws_cognito_user_pool.main.id
  generate_secret     = false
  explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}

resource "aws_lambda_permission" "cognito_invoke" {
  statement_id  = "AllowCognitoInvoke"
  action        = "lambda:InvokeFunction"
  function_name = module.create_account.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.main.arn
}