# This EC2 import draft was superseded by the user's Fargate request before apply.
# Bootstrap state storage is live; this root has not imported or changed the beta.
variable "legacy_ec2_migration_enabled" {
  type    = bool
  default = false
  validation {
    condition     = var.legacy_ec2_migration_enabled
    error_message = "EC2 provisioning is superseded. This root is a migration draft only; use the Fargate redesign and do not apply it as the product runtime."
  }
}
