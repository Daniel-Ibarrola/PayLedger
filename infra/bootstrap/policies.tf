# What the CI roles may do.
#
# The read document is what `terraform plan` needs and nothing more. The write
# document below is the incremental permissions apply needs on top of that.
# They used to be composed into one "apply" document via
# source_policy_documents, which kept a resource's read actions listed once
# instead of twice — but the combined JSON crossed IAM's 6144-character
# customer-managed-policy-size quota. So instead the apply ROLE gets both
# policies attached directly: aws_iam_policy.plan (read) and aws_iam_policy.
# apply (write). Same "listed once" property, no size limit shared between
# them, at the cost of the read/write split now living in two resources
# instead of one document.
#
# Actions are enumerated rather than globbed for the same reason iam.tf in the
# main config enumerates GetItem and PutItem: a "lambda:*" here is the pattern
# the next role inherits.

data "aws_iam_policy_document" "read" {
  statement {
    sid       = "StateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "StateRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/${local.state_key}"]
  }

  # Terraform takes a state lock for `plan`, not just `apply`, and with S3
  # native locking that lock IS an object written next to the state. So even the
  # read-only role needs PutObject and DeleteObject — scoped to the .tflock key
  # and nothing else, which is why this is a separate statement.
  statement {
    sid    = "StateLock"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]

    resources = ["${aws_s3_bucket.state.arn}/${local.state_key}.tflock"]
  }

  statement {
    sid    = "ReadDynamoDB"
    effect = "Allow"

    # Table metadata only. Note the absence of GetItem, Query and Scan: a role
    # that deploys the table has no business reading rows out of it.
    actions = [
      "dynamodb:DescribeTable",
      "dynamodb:DescribeContinuousBackups",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:ListTagsOfResource",
    ]

    resources = [local.arn.tables]
  }

  statement {
    sid    = "ReadLambda"
    effect = "Allow"

    actions = [
      "lambda:GetFunction",
      "lambda:GetFunctionCodeSigningConfig",
      "lambda:GetFunctionConcurrency",
      "lambda:GetPolicy",
      "lambda:ListVersionsByFunction",
      "lambda:ListTags",
    ]

    resources = [local.arn.functions]
  }

  statement {
    sid    = "ReadLambdaLayers"
    effect = "Allow"

    actions = [
      "lambda:GetLayerVersion",
      "lambda:ListLayerVersions",
    ]

    resources = local.arn.layers
  }

  statement {
    sid       = "ReadApiGateway"
    effect    = "Allow"
    actions   = ["apigateway:GET"]
    resources = local.arn.apis
  }

  statement {
    sid     = "ReadLogGroups"
    effect  = "Allow"
    actions = ["logs:DescribeLogGroups"]

    # DescribeLogGroups is a prefix query over the account's log groups and
    # ignores anything narrower than log-group:* — scoping it tighter silently
    # returns nothing rather than erroring, which is a worse failure. It exposes
    # group names, never their contents.
    resources = ["arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:*"]
  }

  statement {
    sid    = "ReadLogGroupTags"
    effect = "Allow"

    actions = [
      "logs:ListTagsForResource",
      "logs:ListTagsLogGroup",
    ]

    resources = local.arn.log_groups
  }

  statement {
    sid    = "ReadBudgets"
    effect = "Allow"

    # Budgets collapses its whole read surface onto ViewBudget — there is no
    # DescribeBudget action to grant. Tag reads are separate, and the refresh
    # makes both calls.
    actions = [
      "budgets:ViewBudget",
      "budgets:ListTagsForResource",
    ]

    resources = [local.arn.budgets]
  }

  statement {
    sid    = "ReadIamRoles"
    effect = "Allow"

    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRoleTags",
    ]

    resources = [local.arn.roles]
  }

  statement {
    sid    = "ReadCognito"
    effect = "Allow"

    actions = [
      "cognito-idp:DescribeUserPool",
      "cognito-idp:DescribeUserPoolClient",
      "cognito-idp:ListTagsForResource",
      # MFA config is exposed through its own Get/Set API pair rather than
      # folded into DescribeUserPool/UpdateUserPool, even though it's just
      # another user pool setting — the provider reads and writes it as a
      # separate call regardless of whether mfa_configuration is set in config.
      "cognito-idp:GetUserPoolMfaConfig",
    ]

    resources = local.arn.user_pools
  }

  statement {
    sid    = "ReadKMS"
    effect = "Allow"

    actions = [
      "kms:DescribeKey",
      "kms:GetKeyPolicy",
      "kms:GetKeyRotationStatus",
      "kms:ListResourceTags",
    ]

    resources = [local.arn.kms_key]
  }

  # ListAliases has no resource-level permissions to scope to, same as
  # DescribeLogGroups above.
  statement {
    sid       = "ReadKMSAliases"
    effect    = "Allow"
    actions   = ["kms:ListAliases"]
    resources = ["*"]
  }

  statement {
    sid    = "ReadAwsManagedPolicies"
    effect = "Allow"

    actions = [
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
    ]

    # AWS's own managed policies only. Refreshing the function role's
    # AWSLambdaBasicExecutionRole attachment reads the policy behind it; these
    # are world-readable documents, not account resources.
    resources = ["arn:aws:iam::aws:policy/*"]
  }

  statement {
    sid    = "ReadIamCustomerPolicies"
    effect = "Allow"

    actions = [
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:ListPolicyTags",
    ]

    resources = [local.arn.iam_policies]
  }

  statement {
    sid       = "ReadScheduler"
    effect    = "Allow"
    actions   = ["scheduler:GetSchedule"]
    resources = [local.arn.schedules]
  }
}

data "aws_iam_policy_document" "apply" {
  statement {
    sid       = "StateWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.state.arn}/${local.state_key}"]
  }

  statement {
    sid    = "WriteDynamoDB"
    effect = "Allow"

    # Table lifecycle only — still no data-plane actions. Deleting the table
    # destroys the rows, but nothing here can read one out.
    actions = [
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:UpdateTable",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
    ]

    resources = [local.arn.tables]
  }

  statement {
    sid    = "WriteLambda"
    effect = "Allow"

    actions = [
      "lambda:CreateFunction",
      "lambda:DeleteFunction",
      "lambda:UpdateFunctionCode",
      "lambda:UpdateFunctionConfiguration",
      "lambda:PutFunctionConcurrency",
      "lambda:DeleteFunctionConcurrency",
      "lambda:AddPermission",
      "lambda:RemovePermission",
      "lambda:TagResource",
      "lambda:UntagResource",
    ]

    resources = [local.arn.functions]
  }

  statement {
    sid    = "WriteLambdaLayers"
    effect = "Allow"

    actions = [
      "lambda:PublishLayerVersion",
      "lambda:DeleteLayerVersion",
    ]

    resources = local.arn.layers
  }

  statement {
    sid    = "WriteApiGateway"
    effect = "Allow"

    actions = [
      "apigateway:POST",
      "apigateway:PUT",
      "apigateway:PATCH",
      "apigateway:DELETE",
    ]

    resources = local.arn.apis
  }

  statement {
    sid    = "WriteLogGroups"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:DeleteRetentionPolicy",
      "logs:TagResource",
      "logs:UntagResource",
      "logs:TagLogGroup",
      "logs:UntagLogGroup",
      "logs:AssociateKmsKey",
      "logs:DisassociateKmsKey",
    ]

    resources = local.arn.log_groups
  }

  statement {
    sid    = "WriteKMS"
    effect = "Allow"

    actions = [
      "kms:PutKeyPolicy",
      "kms:EnableKeyRotation",
      "kms:DisableKeyRotation",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:ScheduleKeyDeletion",
      "kms:CreateGrant",
    ]

    resources = [local.arn.kms_key]
  }

  statement {
    sid       = "CreateKMSKey"
    effect    = "Allow"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
  }

  statement {
    sid    = "WriteKMSAlias"
    effect = "Allow"

    actions = [
      "kms:CreateAlias",
      "kms:DeleteAlias",
    ]

    resources = [local.arn.kms_alias, local.arn.kms_key]
  }

  # The API Gateway stage's access_log_settings is not a property of the log
  # group — it creates a "log delivery", an account-level resource with no ARN
  # to scope to. Without these the apply fails on the stage with an
  # AccessDenied that names none of them, which is a genuinely awful hour.
  statement {
    sid    = "ApiGatewayAccessLogDelivery"
    effect = "Allow"

    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
    ]

    resources = ["*"]
  }

  statement {
    sid    = "WriteCognito"
    effect = "Allow"

    actions = [
      "cognito-idp:CreateUserPool",
      "cognito-idp:UpdateUserPool",
      "cognito-idp:DeleteUserPool",
      # Custom (schema-block) attributes are added via their own API rather
      # than folded into UpdateUserPool, same separate-API quirk as MFA config.
      "cognito-idp:AddCustomAttributes",
      "cognito-idp:CreateUserPoolClient",
      "cognito-idp:UpdateUserPoolClient",
      "cognito-idp:DeleteUserPoolClient",
      "cognito-idp:TagResource",
      "cognito-idp:UntagResource",
      # Paired with GetUserPoolMfaConfig above — same separate-API quirk.
      "cognito-idp:SetUserPoolMfaConfig",
    ]

    resources = local.arn.user_pools
  }

  statement {
    sid    = "WriteBudgets"
    effect = "Allow"

    # ModifyBudget is the single action behind CreateBudget, UpdateBudget and
    # DeleteBudget, and behind the notification calls too — the API is
    # fine-grained but the IAM surface is not, so this is as narrow as it gets.
    # The scoping that matters is the resource: payledger-* budgets only.
    actions = [
      "budgets:ModifyBudget",
      "budgets:TagResource",
      "budgets:UntagResource",
    ]

    resources = [local.arn.budgets]
  }

  statement {
    sid    = "WriteIamRoles"
    effect = "Allow"

    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
    ]

    resources = [local.arn.roles]
  }

  # Creating a Lambda, or an EventBridge Scheduler schedule with a role_arn
  # target, hands that principal a role. IAM treats that as a privilege
  # transfer and gates it behind PassRole; the condition stops the role being
  # passed to any service other than the two this config actually uses.
  statement {
    sid       = "PassExecutionRole"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [local.arn.roles]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }

  statement {
    sid    = "WriteIamCustomerPolicies"
    effect = "Allow"

    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]

    resources = [local.arn.iam_policies]
  }

  statement {
    sid    = "WriteScheduler"
    effect = "Allow"

    actions = [
      "scheduler:CreateSchedule",
      "scheduler:UpdateSchedule",
      "scheduler:DeleteSchedule",
    ]

    resources = [local.arn.schedules]
  }

  statement {
    sid       = "DenyAttachingOtherManagedPolicies"
    effect    = "Deny"
    actions   = ["iam:AttachRolePolicy"]
    resources = ["*"]

    condition {
      test     = "ArnNotEquals"
      variable = "iam:PolicyARN"
      values = [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
      ]
    }

    condition {
      test     = "StringNotLike"
      variable = "iam:PolicyARN"
      values   = [local.arn.iam_policies]
    }
  }

  # The gha-* naming already keeps the CI roles out of the payledger-* Allows.
  # This turns that from a convention someone breaks later into a guarantee: a
  # Deny wins over any Allow, so even a future policy widened to iam:* on "*"
  # cannot let a deploy rewrite its own permissions, the OIDC trust, or a user.
  statement {
    sid     = "DenyTouchingCiIdentities"
    effect  = "Deny"
    actions = ["iam:*"]

    resources = [
      "arn:aws:iam::${local.account_id}:role/gha-*",
      "arn:aws:iam::${local.account_id}:policy/gha-*",
      "arn:aws:iam::${local.account_id}:oidc-provider/*",
      "arn:aws:iam::${local.account_id}:user/*",
    ]
  }
}

resource "aws_iam_policy" "plan" {
  name        = "gha-${var.project}-plan"
  description = "Read-only Terraform plan permissions for ${var.github_repo}."
  policy      = data.aws_iam_policy_document.read.json
}

resource "aws_iam_policy" "apply" {
  name        = "gha-${var.project}-apply"
  description = "Terraform apply write permissions for ${var.github_repo}, scoped to ${var.project}-* resources. Read permissions come from aws_iam_policy.plan, attached alongside this one."
  policy      = data.aws_iam_policy_document.apply.json
}

resource "aws_iam_role_policy_attachment" "plan" {
  role       = aws_iam_role.github["plan"].name
  policy_arn = aws_iam_policy.plan.arn
}

# The apply role needs the read policy too — plan-equivalent visibility into
# what it's about to change — attached separately rather than folded into
# aws_iam_policy.apply because the combined JSON exceeds IAM's per-policy size
# quota (see the file header comment).
resource "aws_iam_role_policy_attachment" "apply_read" {
  role       = aws_iam_role.github["apply"].name
  policy_arn = aws_iam_policy.plan.arn
}

resource "aws_iam_role_policy_attachment" "apply" {
  role       = aws_iam_role.github["apply"].name
  policy_arn = aws_iam_policy.apply.arn
}
