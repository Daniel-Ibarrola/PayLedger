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
    sid    = "CreateAccountWrite"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
    ]

    resources = [aws_dynamodb_table.ledger.arn]

    # Write-only and scoped to ACCT# items, mirroring the Merchant Service's
    # condition-guarded pattern (design doc: IAM section) — no GetItem, no
    # Query, no index. The handler never reads: its conditional PutItem
    # (attribute_not_exists(PK)) is the existence check.
    condition {
      test     = "StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = ["ACCT#*"]
    }
  }
}

resource "aws_iam_role_policy" "create_account_dynamodb" {
  name   = "dynamodb-access"
  role   = module.create_account.role_name
  policy = data.aws_iam_policy_document.create_account_dynamodb.json
}
