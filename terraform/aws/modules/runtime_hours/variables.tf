variable "name" { type = string }
variable "cluster_arn" { type = string }
variable "service_arn" { type = string }
variable "database_identifier" { type = string }
variable "database_arn" { type = string }
variable "renderer_family" { type = string }
variable "enabled" {
  type        = bool
  default     = false
  description = "Enable only after Fargate persistence, shutdown and renderer acceptance."
}
