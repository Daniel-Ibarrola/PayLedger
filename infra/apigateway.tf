
# COGNITO_USER_POOLS authorizers only exist on REST APIs. This is an HTTP API
# (protocol_type = "HTTP" below), whose native equivalent is a JWT authorizer
# pointed at the user pool's own token issuer.
resource "aws_apigatewayv2_authorizer" "cognito_auth" {
  api_id           = aws_apigatewayv2_api.main.id
  name             = "cognito-authorizer"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.main.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.main.id}"
  }
}

resource "aws_apigatewayv2_api" "main" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "authorization_service" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_uri        = module.authorization_service.invoke_arn
  payload_format_version = "2.0"
}

# The route keys here are the exact strings the handler's ROUTES dict is keyed
# on. If they drift, every request 404s from inside the Lambda.
resource "aws_apigatewayv2_route" "create_authorization" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "POST /authorizations"
  target    = "integrations/${aws_apigatewayv2_integration.authorization_service.id}"

  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito_auth.id
}

resource "aws_cloudwatch_log_group" "api_access" {
  name              = "/aws/apigateway/${local.name_prefix}-api"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_access.arn
    # integrationErrorMessage is the field that distinguishes "the Lambda
    # returned a 500" from "API Gateway could not reach the Lambda at all".
    format = jsonencode({
      requestId               = "$context.requestId"
      requestTime             = "$context.requestTime"
      httpMethod              = "$context.httpMethod"
      routeKey                = "$context.routeKey"
      path                    = "$context.path"
      status                  = "$context.status"
      responseLatency         = "$context.responseLatency"
      integrationStatus       = "$context.integrationStatus"
      integrationErrorMessage = "$context.integrationErrorMessage"
    })
  }

  default_route_settings {
    # A throttle on a personal account is a cost control, not a capacity plan:
    # it caps the blast radius of a runaway loop or an unauthenticated endpoint
    # being found. The API is public until Cognito lands in week 2.
    throttling_burst_limit = 20
    throttling_rate_limit  = 10
  }
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = module.authorization_service.function_name
  principal     = "apigateway.amazonaws.com"

  # Scoped to this API; the /*/* covers any stage and any route on it.
  source_arn = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}
