resource "aws_secretsmanager_secret" "openai" {
  description = "Operator supplies the OpenAI key before explicit AI activation; no value in template"
  name        = "ApiSecret-bQAoakndLQlN"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = "fuminosuke-beta-535562976288"
  managed_login_version = 1
  user_pool_id          = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool" "main" {
  auto_verified_attributes = []
  deletion_protection      = "ACTIVE"
  mfa_configuration        = "OFF"
  name                     = "fuminosuke-beta"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  user_pool_tier      = "LITE"
  username_attributes = []
  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }
  admin_create_user_config {
    allow_admin_create_user_only = true
  }
  email_configuration {
    email_sending_account = "COGNITO_DEFAULT"
  }
  password_policy {
    minimum_length                   = 12
    password_history_size            = 0
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 3
  }
  sign_in_policy {
    allowed_first_auth_factors = ["PASSWORD"]
  }
  username_configuration {
    case_sensitive = false
  }
  verification_message_template {
    default_email_option = "CONFIRM_WITH_CODE"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role_policy" "persistence" {
  name = "BetaPersistence"
  policy = jsonencode({
    Statement = [{
      Action = ["s3:ListBucket"]
      Condition = {
        StringLike = {
          "s3:prefix" = ["${var.installation_id}/*"]
        }
      }
      Effect   = "Allow"
      Resource = ["${aws_s3_bucket.journal.arn}", "${aws_s3_bucket.backup.arn}"]
      }, {
      Action   = ["s3:GetObject", "s3:PutObject"]
      Effect   = "Allow"
      Resource = ["${aws_s3_bucket.journal.arn}/${var.installation_id}/*", "${aws_s3_bucket.backup.arn}/${var.installation_id}/*"]
      }, {
      Action   = ["secretsmanager:GetSecretValue"]
      Effect   = "Allow"
      Resource = aws_secretsmanager_secret.openai.arn
    }]
    Version = "2012-10-17"
  })
  role = aws_iam_role.host.name
}

resource "aws_cognito_user_pool_client" "web" {
  access_token_validity                         = 1
  allowed_oauth_flows                           = ["code"]
  allowed_oauth_flows_user_pool_client          = true
  allowed_oauth_scopes                          = ["openid"]
  auth_session_validity                         = 3
  callback_urls                                 = ["https://${var.public_hostname}/api/auth/callback"]
  enable_propagate_additional_user_context_data = false
  enable_token_revocation                       = true
  explicit_auth_flows                           = []
  id_token_validity                             = 1
  logout_urls                                   = ["https://${var.public_hostname}/"]
  name                                          = "UserPoolClient-AJ858nGqHaev"
  prevent_user_existence_errors                 = "ENABLED"
  read_attributes                               = []
  refresh_token_validity                        = 1
  supported_identity_providers                  = ["COGNITO"]
  user_pool_id                                  = aws_cognito_user_pool.main.id
  write_attributes                              = []
  token_validity_units {
    access_token  = "hours"
    id_token      = "hours"
    refresh_token = "days"
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  role       = aws_iam_role.host.name
}

resource "aws_iam_instance_profile" "host" {
  name = "fuminosuke-beta-Profile-4CXkbIVmZaPc"
  path = "/"
  role = aws_iam_role.host.name
  tags = {}
}

resource "aws_iam_role" "host" {
  assume_role_policy = jsonencode({
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
    Version = "2012-10-17"
  })
  force_detach_policies = false
  max_session_duration  = 3600
  name                  = "fuminosuke-beta-InstanceRole-MadM8YzqJsrj"
  path                  = "/"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
}
