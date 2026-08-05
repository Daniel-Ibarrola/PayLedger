data "aws_iam_policy_document" "ledger_table_write" {
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
  policy = data.aws_iam_policy_document.ledger_table_write.json
}

data "aws_iam_policy_document" "create_account_dynamodb" {
  statement {
    sid    = "CreateAccountReadWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
    ]

    resources = [aws_dynamodb_table.ledger.arn]
  }
}

resource "aws_iam_role_policy" "create_account_dynamodb" {
  name   = "dynamodb-access"
  role   = module.create_account.role_name
  policy = data.aws_iam_policy_document.create_account_dynamodb.json
}
