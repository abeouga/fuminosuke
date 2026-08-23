variable "project_id" {
  description = "Google Cloud project ID used for the OAuth app, Google Sheets API, and Google Drive API."
  type        = string
}

variable "enabled_apis" {
  description = "Google APIs enabled for the local OCR application."
  type        = set(string)
  default = [
    "drive.googleapis.com",
    "picker.googleapis.com",
    "sheets.googleapis.com",
  ]
}
