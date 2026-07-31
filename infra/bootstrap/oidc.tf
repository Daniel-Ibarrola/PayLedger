# The GitHub OIDC trust relationship.

# The provider already exists in this account, created by hand before this
# config did, and other repositories' roles trust it too.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

locals {
  owner = split("/", var.github_repo)[0]
  name  = split("/", var.github_repo)[1]

  # GitHub mints the `sub` claim in two forms, and which one a repository sends
  # is not something the workflow controls:
  #
  #   repo:Daniel-Ibarrola/PayLedger:...                          (documented)
  #   repo:Daniel-Ibarrola@67239490/PayLedger@1317597421:...      (immutable)
  #
  # The second appends the numeric owner and repo ids, so trust survives a
  # rename and does not transfer to whoever claims the freed-up name. This repo
  # sends it. Both are listed because StringEquals over a list is an OR of exact
  # matches — no wildcard, and no breakage if GitHub flips the format back.
  repo_ids = [
    var.github_repo,
    "${local.owner}@${var.github_owner_id}/${local.name}@${var.github_repo_id}",
  ]

  github_subjects = {
    # Every pull_request run sends this sub whatever branch the PR is from —
    # which is why the role it unlocks is read-only.
    plan = [for r in local.repo_ids : "repo:${r}:pull_request"]

    # Bound to the main branch ref, so write access follows the merge, not the
    # push. No other branch, tag, or PR can assume it.
    apply = [for r in local.repo_ids : "repo:${r}:ref:refs/heads/main"]
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
      values   = each.value
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
