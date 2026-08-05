# The role, assume-role policy, and CloudWatch Logs managed policy attachment
# live in the lambda_function module (see authorization-service-lambda.tf).
# Everything the function actually touches beyond logging gets an inline,
# resource-scoped policy here — the design doc's IAM section calls for one
# role per function with no shared blanket policy.
data "aws_iam_policy_document" "authorization_service_dynamodb" {
  statement {
    sid    = "AuthrizationWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]

    resources = [aws_dynamodb_table.ledger.arn]
  }
}

resource "aws_iam_role_policy" "authorization_service_dynamodb" {
  name   = "dynamodb-access"
  role   = module.authorization_service.role_name
  policy = data.aws_iam_policy_document.authorization_service_dynamodb.json
}
