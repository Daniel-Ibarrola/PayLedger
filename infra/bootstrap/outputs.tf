# These four values are what the rest of the setup is wired from: the first two
# go into the main config's backend block, the last two into deploy.yml.

output "state_bucket" {
  description = "S3 bucket holding the main config's state. Goes in its backend block."
  value       = aws_s3_bucket.state.id
}

output "state_key" {
  description = "State object key. Must match the main config's backend `key`."
  value       = local.state_key
}

output "plan_role_arn" {
  description = "Read-only role assumed by the PR plan job."
  value       = aws_iam_role.github["plan"].arn
}

output "apply_role_arn" {
  description = "Deploy role assumed by the main-branch apply job."
  value       = aws_iam_role.github["apply"].arn
}
