resource "aws_cognito_user_pool" "main" {
  name = "${local.name_prefix}-user-pool"

    # Auto-verified email attributes
  auto_verified_attributes = ["email"]

  schema {
    name              = "email"
    attribute_data_type = "String"
    mutable           = true
    required          = true
  }

  schema {
    name              = "name"
    attribute_data_type = "String"
    mutable           = true
  }
}

resource "aws_cognito_user_pool_client" "main" {
  name                = "${local.name_prefix}-client"
  user_pool_id        = aws_cognito_user_pool.main.id
  generate_secret     = false
  explicit_auth_flows = ["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
}
