terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # State is local for now. It moves to an S3 backend with DynamoDB locking
  # before anything but this laptop applies against the account — see the
  # runbook. Keeping it local while the resource set is a toy table avoids
  # bootstrapping a backend for something that gets destroyed daily.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  name_prefix = "${var.project}-${var.environment}"

  # Repo root, relative to infra/. Everything that packages source code hangs
  # off this so the paths survive being run from a different directory.
  src_dir = "${path.module}/.."
}
