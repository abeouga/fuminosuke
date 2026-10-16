terraform {
  backend "s3" {
    bucket              = "fuminosuke-tfstate-535562976288-ap-northeast-1"
    key                 = "bootstrap/terraform.tfstate"
    region              = "ap-northeast-1"
    profile             = "abe-deploy-user"
    allowed_account_ids = ["535562976288"]
    encrypt             = true
    use_lockfile        = true
  }
}
