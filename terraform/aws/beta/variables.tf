variable "public_hostname" {
  type    = string
  default = "herdio-system.com"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]+\\.[a-z]{2,}$", var.public_hostname))
    error_message = "Set the exact public hostname without scheme or path."
  }
}

variable "installation_id" {
  type    = string
  default = "65bdbf79-4ee6-4a0b-ac80-2c6eb662d845"
  validation {
    condition     = var.installation_id == "65bdbf79-4ee6-4a0b-ac80-2c6eb662d845"
    error_message = "This deployment owns an immutable journal identity; do not reset its budget by changing it."
  }
}

variable "notification_email" {
  type      = string
  sensitive = true
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.notification_email))
    error_message = "Provide the operator-authorized AWS budget notification address."
  }
}
