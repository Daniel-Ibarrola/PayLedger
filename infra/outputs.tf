output "api_base_url" {
  description = "Base URL of the HTTP API. The e2e tests read this."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "toy_table_name" {
  description = "Name of the toy DynamoDB table."
  value       = aws_dynamodb_table.toy_items.name
}

output "items_service_function_name" {
  description = "Lambda function name, for `aws logs tail` and manual invokes."
  value       = aws_lambda_function.items_service.function_name
}

output "items_service_log_group" {
  description = "CloudWatch log group for the function."
  value       = aws_cloudwatch_log_group.items_service.name
}
