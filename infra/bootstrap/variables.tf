variable "aws_region" {
  description = "Region the state bucket lives in. Must match the main config's aws_region."
  type        = string
  default     = "us-east-2"
}

variable "project" {
  description = "Name prefix. Must match the main config's project variable."
  type        = string
  default     = "payledger"
}

variable "environment" {
  description = "Environment whose state key this bootstraps."
  type        = string
  default     = "dev"
}

variable "github_repo" {
  description = <<-EOT
    owner/name of the repository allowed to assume the CI roles. This is the
    entire access control boundary — a typo here either locks CI out or, worse,
    grants a repository you do not control.
  EOT
  type        = string
  default     = "Daniel-Ibarrola/PayLedger"

  validation {
    condition     = can(regex("^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$", var.github_repo))
    error_message = "github_repo must be in owner/name form, with no leading https:// or trailing .git."
  }
}

# The numeric ids below appear in the immutable form of the OIDC `sub` claim.
# Both come from one unauthenticated call:
#
#   curl -s https://api.github.com/repos/Daniel-Ibarrola/PayLedger \
#     | jq '{repo: .id, owner: .owner.id}'
#
# They never change, including across a rename — that is the point of them.

variable "github_owner_id" {
  description = "Numeric id of the repository owner (.owner.id from the GitHub API)."
  type        = number
  default     = 67239490
}

variable "github_repo_id" {
  description = "Numeric id of the repository (.id from the GitHub API)."
  type        = number
  default     = 1317597421
}
