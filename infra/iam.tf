data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "authorization_service" {
  name               = "${local.name_prefix}-authorization-service"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Managed policy only for the CloudWatch Logs plumbing. Everything the function
# actually touches gets an inline, resource-scoped policy — the design doc's IAM
# section calls for one role per function with no shared blanket policy.
resource "aws_iam_role_policy_attachment" "authorization_service_basic_execution" {
  role       = aws_iam_role.authorization_service.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

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
  role   = aws_iam_role.authorization_service.id
  policy = data.aws_iam_policy_document.authorization_service_dynamodb.json
}
