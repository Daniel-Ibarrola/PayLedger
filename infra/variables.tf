variable "aws_region" {
  description = "Region everything is deployed into."
  type        = string
  default     = "us-east-2"
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "payledger"
}

variable "environment" {
  description = "Deployment environment; part of every resource name."
  type        = string
  default     = "dev"
}

variable "python_runtime" {
  description = "Lambda runtime. Must match requires-python in pyproject.toml."
  type        = string
  default     = "python3.13"
}

variable "lambda_architecture" {
  description = "arm64 is ~20% cheaper per GB-second than x86_64 and the code is pure Python."
  type        = string
  default     = "arm64"

  validation {
    condition     = contains(["arm64", "x86_64"], var.lambda_architecture)
    error_message = "lambda_architecture must be arm64 or x86_64."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention. Never leave this unset — the default is forever, which bills forever."
  type        = number
  default     = 14
}
