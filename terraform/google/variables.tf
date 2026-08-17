variable "project_id" {
  description = "Google Cloud project ID used for the OAuth app and Google Sheets API."
  type        = string
}

variable "enabled_apis" {
  description = "Google APIs enabled for the local OCR application."
  type        = set(string)
  default = [
    "sheets.googleapis.com",
  ]
}
