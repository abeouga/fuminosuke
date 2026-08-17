# Google Cloud API setup

This stack only enables the Google Sheets API. The OCR backend and
database continue to run locally; it does not create AWS Lambda resources.

## Authentication for Terraform (gcloud is not required)

Set a Google credential file for Terraform in the current PowerShell session:

```powershell
$env:GOOGLE_APPLICATION_CREDENTIALS = "C:\path\to\terraform-admin.json"
Copy-Item terraform.tfvars.example terraform.tfvars
# Edit project_id in terraform.tfvars.
terraform init
terraform plan
terraform apply
```

The credential needs permission to enable services in the selected project,
for example `roles/serviceusage.serviceUsageAdmin`. The service account key used
by Terraform is infrastructure administration material; it is not used by the
OCR application to access a user's spreadsheet.

## One manual Google Cloud Console step

Google does not expose creation of a normal Google Account OAuth client through
the public API. In **Google Auth Platform** configure Branding, Audience and (for
Testing mode) Test users, then create a **Web application** OAuth client with
this exact authorized redirect URI:

```text
http://127.0.0.1:8000/api/v1/auth/google/callback
```

Copy the resulting client ID and secret to `backend/.env` as documented in the
root README. Never add `terraform.tfvars`, Terraform state, or credential JSON
files to Git.
