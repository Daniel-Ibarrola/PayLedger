data "aws_iam_policy_document" "ledger_table_write" {
  statement {
    sid    = "AuthorizationWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
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
    sid    = "CreateAccountWrite"
    effect = "Allow"

    actions = [
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

data "aws_iam_policy_document" "deposit_service_ledger_write" {
  statement {
    sid    = "AuthorizationWrite"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]

    resources = [aws_dynamodb_table.ledger.arn]
  }
}

resource "aws_iam_role_policy" "deposit_service_dynamodb" {
  name   = "dynamodb-access"
  role   = module.deposit_service.role_name
  policy = data.aws_iam_policy_document.ledger_table_write.json
}