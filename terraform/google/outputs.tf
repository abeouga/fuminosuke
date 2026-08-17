output "enabled_apis" {
  description = "APIs managed by this Terraform stack."
  value       = sort([for service in google_project_service.ocr_apis : service.service])
}

output "oauth_redirect_uri" {
  description = "Register this exact URI on the manually-created Web OAuth client."
  value       = "http://127.0.0.1:8000/api/v1/auth/google/callback"
}
