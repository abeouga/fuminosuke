terraform {
  required_version = ">= 1.15.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
  backend "s3" {
    bucket              = "fuminosuke-tfstate-535562976288-ap-northeast-1"
    key                 = "beta/terraform.tfstate"
    region              = "ap-northeast-1"
    profile             = "abe-deploy-user"
    allowed_account_ids = ["535562976288"]
    encrypt             = true
    use_lockfile        = true
  }
}

provider "aws" {
  profile             = "abe-deploy-user"
  region              = "ap-northeast-1"
  allowed_account_ids = ["535562976288"]
}
