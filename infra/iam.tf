data "aws_iam_policy_document" "authorization_service_ledger_write" {
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

  # A Query carrying IndexName is authorized against the index ARN, not the
  # table's, so the statement above does not reach it.
  statement {
    sid    = "AuthorizationIndexRead"
    effect = "Allow"

    actions = [
      "dynamodb:Query",
    ]

    resources = ["${aws_dynamodb_table.ledger.arn}/index/${local.ledger_gsi1_name}"]
  }

  statement {
    sid    = "AuthorizationKmsForDynamoDB"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]

    resources = [aws_kms_key.payledger_key.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "authorization_service_dynamodb" {
  name   = "dynamodb-access"
  role   = module.authorization_service.role_name
  policy = data.aws_iam_policy_document.authorization_service_ledger_write.json
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

  statement {
    sid    = "CreateAccountKmsForDynamoDB"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]

    resources = [aws_kms_key.payledger_key.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${var.aws_region}.amazonaws.com"]
    }
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

  statement {
    sid    = "DepositKmsForDynamoDB"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
    ]

    resources = [aws_kms_key.payledger_key.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["dynamodb.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deposit_service_dynamodb" {
  name   = "dynamodb-access"
  role   = module.deposit_service.role_name
  policy = data.aws_iam_policy_document.deposit_service_ledger_write.json
}

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "kms_shared_policy" {
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*"
    ]
    resources = ["*"]

    # Without this, the logs service could use the key to encrypt for any log
    # group in the account, not just this project's.
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }

  statement {
    sid    = "EnableIamPolicies"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }
}
