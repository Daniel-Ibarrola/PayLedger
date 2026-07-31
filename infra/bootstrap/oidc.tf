# The GitHub OIDC trust relationship.

# The provider already exists in this account, created by hand before this
# config did, and other repositories' roles trust it too.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  # The `sub` claim is the whole authorisation decision. GitHub mints it from
  # the run's context, and it is not something a workflow can influence.
  github_subjects = {
    # Every pull_request-triggered run gets this exact sub, regardless of which
    # branch the PR comes from — which is precisely why the role it unlocks is
    # read-only. Anyone who can open a PR can assume it.
    plan = "repo:${var.github_repo}:pull_request"

    # Bound to the main branch ref. A run triggered from any other branch, any
    # tag, or any PR cannot assume this role even with `id-token: write`, so
    # write access to AWS follows the merge, not the push.
    apply = "repo:${var.github_repo}:ref:refs/heads/main"
  }
}

data "aws_iam_policy_document" "assume_role" {
  for_each = local.github_subjects

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    # Both conditions are load-bearing. Without the aud check, a token minted
    # for some other OIDC audience would be accepted. Without an exact-match sub
    # — StringEquals, never StringLike with a trailing "*" — every repository
    # that can reach this provider could assume the role.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [each.value]
    }
  }
}

resource "aws_iam_role" "github" {
  for_each = local.github_subjects

  # Named "gha-*", outside the "payledger-*" prefix, on purpose: the apply
  # policy grants IAM writes across payledger-*, and a role matching its own
  # policy's resource pattern could rewrite that policy and escalate to admin.
  # policies.tf backs the naming with an explicit Deny.
  name               = "gha-${var.project}-${each.key}"
  description        = "GitHub Actions Terraform ${each.key} role for ${var.github_repo}."
  assume_role_policy = data.aws_iam_policy_document.assume_role[each.key].json

  # Credentials expire with the job anyway; this caps how long a set that leaks
  # out of a run stays useful. A plan or apply past the hour is stuck, not slow.
  max_session_duration = 3600
}
