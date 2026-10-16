terraform {
  required_providers {
    aws     = { source = "hashicorp/aws", version = "~> 6.0" }
    archive = { source = "hashicorp/archive", version = "~> 2.0" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "archive_file" "controller" {
  type        = "zip"
  source_file = "${path.module}/controller.py"
  output_path = "${path.root}/.terraform/${var.name}-runtime-hours.zip"
}

resource "aws_sqs_queue" "failures" {
  name                      = "${var.name}-runtime-hours-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_cloudwatch_log_group" "controller" {
  name              = "/aws/lambda/${var.name}-runtime-hours"
  retention_in_days = 14
}

resource "aws_iam_role" "controller" {
  name = "${var.name}-runtime-hours"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "controller" {
  role = aws_iam_role.controller.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecs:DescribeServices", "ecs:UpdateService"], Resource = var.service_arn },
      { Effect = "Allow", Action = ["ecs:ListTasks"], Resource = "*", Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } } },
      { Effect = "Allow", Action = ["ecs:StopTask"], Resource = "arn:aws:ecs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:task/${split("/", var.cluster_arn)[1]}/*", Condition = { ArnEquals = { "ecs:cluster" = var.cluster_arn } } },
      { Effect = "Allow", Action = ["rds:DescribeDBInstances", "rds:StartDBInstance", "rds:StopDBInstance"], Resource = var.database_arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.controller.arn}:*" },
      { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.failures.arn }
    ]
  })
}

resource "aws_lambda_function" "controller" {
  function_name                  = "${var.name}-runtime-hours"
  role                           = aws_iam_role.controller.arn
  runtime                        = "python3.12"
  handler                        = "controller.handler"
  filename                       = data.archive_file.controller.output_path
  source_code_hash               = data.archive_file.controller.output_base64sha256
  timeout                        = 45
  memory_size                    = 128
  reserved_concurrent_executions = 1
  environment {
    variables = {
      ENABLED         = tostring(var.enabled)
      CLUSTER_ARN     = var.cluster_arn
      SERVICE_ARN     = var.service_arn
      DB_IDENTIFIER   = var.database_identifier
      DB_ARN          = var.database_arn
      RENDERER_FAMILY = var.renderer_family
    }
  }
  depends_on = [aws_iam_role_policy.controller, aws_cloudwatch_log_group.controller]
}

resource "aws_lambda_function_event_invoke_config" "controller" {
  function_name                = aws_lambda_function.controller.function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
  destination_config {
    on_failure { destination = aws_sqs_queue.failures.arn }
  }
}

resource "aws_iam_role" "scheduler" {
  name = "${var.name}-runtime-hours-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }, ArnEquals = { "aws:SourceArn" = aws_scheduler_schedule_group.hours.arn } }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = aws_lambda_function.controller.arn },
      { Effect = "Allow", Action = "sqs:SendMessage", Resource = aws_sqs_queue.failures.arn }
    ]
  })
}

resource "aws_scheduler_schedule_group" "hours" { name = "${var.name}-runtime-hours" }

resource "aws_scheduler_schedule" "reconcile" {
  name                         = "reconcile-0700-2300-jst"
  group_name                   = aws_scheduler_schedule_group.hours.name
  description                  = "Every minute, enforce open 07:00-23:00 and closed 23:00-07:00 Asia/Tokyo."
  schedule_expression          = "cron(* * * * ? *)"
  schedule_expression_timezone = "Asia/Tokyo"
  state                        = var.enabled ? "ENABLED" : "DISABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.controller.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = "{}"
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 0
    }
    dead_letter_config { arn = aws_sqs_queue.failures.arn }
  }
  depends_on = [aws_iam_role_policy.scheduler, aws_lambda_function_event_invoke_config.controller]
}

resource "aws_cloudwatch_metric_alarm" "failures" {
  alarm_name          = "${var.name}-runtime-hours-failed"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Maximum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  dimensions          = { QueueName = aws_sqs_queue.failures.name }
  alarm_description   = "Runtime scheduling failed. Inspect the dead-letter queue before relying on night-time cost savings."
}
