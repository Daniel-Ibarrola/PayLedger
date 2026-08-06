output "api_base_url" {
  description = "Base URL of the HTTP API. The e2e tests read this."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "ledger_table_name" {
  description = "Name of the toy DynamoDB table."
  value       = aws_dynamodb_table.ledger.name
}

output "items_service_function_name" {
  description = "Lambda function name, for `aws logs tail` and manual invokes."
  value       = module.authorization_service.function_name
}

output "items_service_log_group" {
  description = "CloudWatch log group for the function."
  value       = module.authorization_service.log_group_name
}

output "cognito_user_pool_id" {
  description = "The id of hte user pool"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "The id of the cognito client"
  value       = aws_cognito_user_pool_client.main.id
}