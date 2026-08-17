provider "google" {
  project = var.project_id
}

resource "google_project_service" "ocr_apis" {
  for_each = var.enabled_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
