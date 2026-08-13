variable "name_prefix" {
  description = "Project/environment prefix, e.g. \"payledger-dev\"."
  type        = string
}

variable "service_name" {
  description = "Short service name, e.g. \"authorization-service\". Combined with name_prefix for all resource names."
  type        = string
}

variable "source_dir" {
  description = "Directory containing the Lambda's top-level *.py files (e.g. src/lambdas/<service>)."
  type        = string
}

variable "handler" {
  description = "Lambda handler entrypoint."
  type        = string
  default     = "handler.lambda_handler"
}

variable "runtime" {
  description = "Lambda runtime. Must match requires-python in pyproject.toml."
  type        = string
}

variable "architecture" {
  description = "arm64 or x86_64."
  type        = string
}

variable "memory_size" {
  description = "Lambda memory, in MB."
  type        = number
  default     = 512
}

variable "timeout" {
  description = "Lambda timeout, in seconds."
  type        = number
  default     = 10
}

variable "environment_variables" {
  description = "Environment variables for the function."
  type        = map(string)
  default     = {}
}

variable "log_retention_days" {
  description = "CloudWatch log retention. Never leave this unset - the default is forever, which bills forever."
  type        = number
}

variable "use_shared_layer" {
  description = "Whether to attach the shared Lambda layer to this function."
  type        = bool
  default     = true
}

variable "shared_layer_arn" {
  description = "ARN of the shared Lambda layer version. Required when use_shared_layer is true."
  type        = string
  default     = ""

  validation {
    condition     = var.shared_layer_arn != "" || !var.use_shared_layer
    error_message = "shared_layer_arn must be set when use_shared_layer is true."
  }
}

variable "kms_key_arn" {
  description = "The ARN of the key used to encrypt Cloudwatch Logs"
  type        = string
}