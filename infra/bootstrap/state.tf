# The remote state backend.
#
# Locking is S3-native (`use_lockfile = true` in the main config's backend
# block), which writes a .tflock object next to the state using a conditional
# PUT. That replaced the DynamoDB lock table in Terraform 1.10 — there is no
# second resource to create here, and nothing to pay for when idle.

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket

  # This bucket is the only record of what exists in the account. Destroying it
  # means re-importing every resource by hand, so it does not get to be
  # collateral damage of a `terraform destroy` in the wrong directory.
  lifecycle {
    prevent_destroy = true
  }
}

# Non-negotiable for state: a truncated write or a bad apply has no other undo.
# Versioning is what makes those recoverable rather than terminal.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

# State holds every resource attribute in plaintext, including anything that
# would be a secret elsewhere. SSE-S3 rather than KMS: KMS would add a key
# policy that every CI role needs listing in, for no gain over a bucket only
# two roles can reach.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "state" {
  # The bucket policy is a second, independent gate: even a role whose IAM
  # policy allows s3:GetObject cannot read state over plain HTTP.
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state.json

  # Applying a policy before public access is blocked briefly leaves a bucket
  # with a policy and no block; ordering removes the window.
  depends_on = [aws_s3_bucket_public_access_block.state]
}

# Versioning means one retained object per apply, forever. 90 days is well past
# any point at which rolling back to an old state is still the right move, and
# stops the bucket becoming a slowly growing bill.
resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-noncurrent-state"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}
