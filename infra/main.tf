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


  backend "s3" {
    bucket = "payledger-tfstate-668144156539"
    key    = "payledger/dev/terraform.tfstate"
    region = "us-east-2"
    use_lockfile = true
    encrypt      = true
  }
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
