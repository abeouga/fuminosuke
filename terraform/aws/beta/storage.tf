resource "aws_s3_bucket_policy" "journal" {
  bucket = aws_s3_bucket.journal.id
  policy = jsonencode({
    Statement = [{
      Action = "s3:*"
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
      Effect    = "Deny"
      Principal = "*"
      Resource  = ["${aws_s3_bucket.journal.arn}", "${aws_s3_bucket.journal.arn}/*"]
      Sid       = "TlsOnly"
      }, {
      Action    = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
      Effect    = "Deny"
      Principal = "*"
      Resource  = "${aws_s3_bucket.journal.arn}/*"
      Sid       = "NeverDeleteReceipts"
      }, {
      Action = "s3:PutObject"
      Condition = {
        Null = {
          "s3:if-none-match" = "true"
        }
      }
      Effect    = "Deny"
      Principal = "*"
      Resource  = "${aws_s3_bucket.journal.arn}/*"
      Sid       = "ConditionalCreationOnly"
    }]
    Version = "2012-10-17"
  })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "journal" {
  bucket = aws_s3_bucket.journal.id
  rule {
    blocked_encryption_types = ["SSE-C"]
    bucket_key_enabled       = false
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "backup" {
  bucket = aws_s3_bucket.backup.id
  policy = jsonencode({
    Statement = [{
      Action = "s3:*"
      Condition = {
        Bool = {
          "aws:SecureTransport" = "false"
        }
      }
      Effect    = "Deny"
      Principal = "*"
      Resource  = ["${aws_s3_bucket.backup.arn}", "${aws_s3_bucket.backup.arn}/*"]
      }, {
      Action = "s3:PutObject"
      Condition = {
        Null = {
          "s3:if-none-match" = "true"
        }
      }
      Effect    = "Deny"
      Principal = "*"
      Resource  = "${aws_s3_bucket.backup.arn}/*"
    }]
    Version = "2012-10-17"
  })
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    blocked_encryption_types = ["SSE-C"]
    bucket_key_enabled       = false
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket" "journal" {
  bucket              = "fuminosuke-beta-journalbucket-vt07rkxkzzdq"
  bucket_namespace    = "global"
  force_destroy       = false
  object_lock_enabled = false
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_ownership_controls" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "backup" {
  block_public_acls       = true
  block_public_policy     = true
  bucket                  = aws_s3_bucket.backup.id
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backup" {
  bucket                                 = aws_s3_bucket.backup.id
  transition_default_minimum_object_size = "all_storage_classes_128K"
  rule {
    id     = "SevenDayRecovery"
    status = "Enabled"
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
    expiration {
      days = 7
    }
    filter {
      prefix = "${var.installation_id}/backups/"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "journal" {
  block_public_acls       = true
  block_public_policy     = true
  bucket                  = aws_s3_bucket.journal.id
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "journal" {
  bucket = aws_s3_bucket.journal.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket" "backup" {
  bucket              = "fuminosuke-beta-backupbucket-qfvbkownbpey"
  bucket_namespace    = "global"
  force_destroy       = false
  object_lock_enabled = false
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  lifecycle { prevent_destroy = true }
}

resource "aws_s3_bucket_versioning" "journal" {
  bucket = aws_s3_bucket.journal.id
  versioning_configuration {
    mfa_delete = "Disabled"
    status     = "Enabled"
  }
}
