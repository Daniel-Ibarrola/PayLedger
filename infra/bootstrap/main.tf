terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is local, and stays local. This config creates the bucket that holds
  # the main config's state, so it cannot keep its own state there without a
  # circular dependency. It is applied by hand with admin credentials and then
  # left alone — nothing here changes on a deploy, so there is no CI run whose
  # state would need to be shared.
  #
  # If it is ever lost, `terraform import` on four resources rebuilds it.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      Component = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Bucket names are globally unique across every AWS account, not just this
  # one, so the account id is the least surprising thing to disambiguate with.
  state_bucket = "${var.project}-tfstate-${local.account_id}"

  # Must match the `key` in the main config's backend block.
  state_key = "${var.project}/${var.environment}/terraform.tfstate"

  # Every resource the main config manages is named with this prefix, which is
  # what makes it possible to scope the deploy role by ARN rather than by "*".
  # The CI roles themselves deliberately sit OUTSIDE it — see policies.tf.
  prefix = "${var.project}-*"

  arn = {
    tables    = "arn:aws:dynamodb:${var.aws_region}:${local.account_id}:table/${local.prefix}"
    functions = "arn:aws:lambda:${var.aws_region}:${local.account_id}:function:${local.prefix}"

    # Both forms are needed: PublishLayerVersion targets the unversioned layer,
    # GetLayerVersion the versioned one.
    layers = [
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:layer:${local.prefix}",
      "arn:aws:lambda:${var.aws_region}:${local.account_id}:layer:${local.prefix}:*",
    ]

    roles = "arn:aws:iam::${local.account_id}:role/${local.prefix}"

    # Likewise: the bare ARN is the group, the ":*" form is what the tagging and
    # retention calls are authorised against.
    log_groups = [
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}:*",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/apigateway/${local.prefix}",
      "arn:aws:logs:${var.aws_region}:${local.account_id}:log-group:/aws/apigateway/${local.prefix}:*",
    ]

    # API Gateway has no account id in its ARNs and no per-API naming to match
    # on, so this is region-wide. It is the one place the scoping is looser than
    # the rest; the account holds no other HTTP APIs in us-east-2.
    apis = [
      "arn:aws:apigateway:${var.aws_region}::/apis",
      "arn:aws:apigateway:${var.aws_region}::/apis/*",
      "arn:aws:apigateway:${var.aws_region}::/tags/*",
    ]
  }
}
