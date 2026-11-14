# This root is an EC2 import draft that is not used: the runtime is designed for Fargate.
# The variable below fails validation unless explicitly enabled, so plan/apply stop here.
variable "legacy_ec2_migration_enabled" {
  type    = bool
  default = false
  validation {
    condition     = var.legacy_ec2_migration_enabled
    error_message = "EC2 provisioning is superseded. This root is a migration draft only; use the Fargate redesign and do not apply it as the product runtime."
  }
}
