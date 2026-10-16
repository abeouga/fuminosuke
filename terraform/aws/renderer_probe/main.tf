terraform {
  required_version = ">= 1.15.0, < 2.0.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
  backend "s3" {
    bucket              = "fuminosuke-tfstate-535562976288-ap-northeast-1"
    key                 = "probe/renderer/terraform.tfstate"
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
  default_tags { tags = { Project = "fuminosuke-beta", Purpose = "phase7b-renderer-probe" } }
}
variable "image_digest" {
  type    = string
  default = ""
  validation {
    condition     = var.image_digest == "" || can(regex("^sha256:[0-9a-f]{64}$", var.image_digest))
    error_message = "Use a verified ECR image digest, never a mutable tag."
  }
}
data "aws_vpc" "existing" { id = "vpc-0daffd24efe21abc3" }
data "aws_subnet" "existing" { id = "subnet-0235f970a5a4bc5a7" }
resource "aws_ecs_cluster" "probe" { name = "fuminosuke-renderer-probe" }
resource "aws_cloudwatch_log_group" "probe" {
  name              = "/fuminosuke/renderer-probe"
  retention_in_days = 7
}
resource "aws_ecr_repository" "probe" {
  name                 = "fuminosuke-renderer-probe"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_ecr_lifecycle_policy" "probe" {
  repository = aws_ecr_repository.probe.name
  policy = jsonencode({ rules = [{ rulePriority = 1, description = "Keep three probe builds",
    selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 3 },
  action = { type = "expire" } }] })
}
resource "aws_security_group" "probe" {
  name        = "fuminosuke-renderer-probe"
  description = "No inbound; HTTPS outbound for ECR and signed S3 transport only"
  vpc_id      = data.aws_vpc.existing.id
  egress {
    protocol    = "tcp"
    from_port   = 443
    to_port     = 443
    cidr_blocks = ["0.0.0.0/0"]
  }
}
resource "aws_s3_bucket" "probe" {
  bucket        = "fuminosuke-renderer-probe-535562976288-ap-northeast-1"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "probe" {
  bucket                  = aws_s3_bucket.probe.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "probe" {
  bucket = aws_s3_bucket.probe.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "probe" {
  bucket = aws_s3_bucket.probe.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Sid = "TlsOnly", Effect = "Deny",
    Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.probe.arn, "${aws_s3_bucket.probe.arn}/*"],
  Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
resource "aws_s3_bucket_lifecycle_configuration" "probe" {
  bucket = aws_s3_bucket.probe.id
  rule {
    id     = "ephemeral-input-output"
    status = "Enabled"
    filter { prefix = "runs/" }
    expiration { days = 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_iam_role" "execution" {
  name = "fuminosuke-renderer-probe-execution"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow",
    Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = "535562976288" },
  ArnLike = { "aws:SourceArn" = "arn:aws:ecs:ap-northeast-1:535562976288:*" } } }] })
}
resource "aws_iam_role_policy" "execution" {
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = aws_ecr_repository.probe.arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.probe.arn}:*" }
  ] })
}
locals {
  image = "${aws_ecr_repository.probe.repository_url}@${var.image_digest}"
  log = { logDriver = "awslogs", options = {
    awslogs-group = aws_cloudwatch_log_group.probe.name,
  awslogs-region = "ap-northeast-1", awslogs-stream-prefix = "probe" } }
}
resource "aws_ecs_task_definition" "probe" {
  count                    = var.image_digest == "" ? 0 : 1
  family                   = "fuminosuke-renderer-probe"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "3072"
  execution_role_arn       = aws_iam_role.execution.arn
  # Deliberately no task_role_arn: neither container receives AWS credentials.
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "input" }
  volume { name = "output" }
  volume { name = "scratch" }
  container_definitions = jsonencode([
    { name            = "transport", image = local.image, essential = true, cpu = 128, memory = 256,
      user            = "10001:10001", readonlyRootFilesystem = true, command = ["/opt/probe/transport.py"],
      linuxParameters = { capabilities = { drop = ["ALL"] }, initProcessEnabled = true }, stopTimeout = 10,
      mountPoints = [{ sourceVolume = "input", containerPath = "/input", readOnly = false },
      { sourceVolume = "output", containerPath = "/output", readOnly = true }],
    logConfiguration = local.log },
    { name            = "native", image = local.image, essential = false, cpu = 896, memory = 2048,
      user            = "10001:10001", readonlyRootFilesystem = true, command = ["/opt/probe/native.py"],
      linuxParameters = { capabilities = { drop = ["ALL"] }, initProcessEnabled = true }, stopTimeout = 10,
      mountPoints = [{ sourceVolume = "input", containerPath = "/input", readOnly = true },
        { sourceVolume = "output", containerPath = "/output", readOnly = false },
        { sourceVolume = "scratch", containerPath = "/scratch", readOnly = false },
      { sourceVolume = "scratch", containerPath = "/tmp", readOnly = false }],
    logConfiguration = local.log }
  ])
  depends_on = [aws_iam_role_policy.execution]
}
output "repository" { value = aws_ecr_repository.probe.repository_url }
output "cluster" { value = aws_ecs_cluster.probe.arn }
output "security_group" { value = aws_security_group.probe.id }
output "subnet" { value = data.aws_subnet.existing.id }
output "bucket" { value = aws_s3_bucket.probe.id }
output "task_definition" { value = try(aws_ecs_task_definition.probe[0].arn, null) }
