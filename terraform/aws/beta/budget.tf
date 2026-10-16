resource "aws_budgets_budget" "monthly" {
  account_id   = "535562976288"
  budget_type  = "COST"
  limit_amount = "56.0"
  limit_unit   = "USD"
  name         = "fuminosuke-beta-monthly"
  tags = {
    Phase   = "beta-acceptance"
    Project = "fuminosuke-beta"
  }
  time_period_end   = "2087-06-15_00:00"
  time_period_start = "2026-10-01_00:00"
  time_unit         = "MONTHLY"
  cost_types {
    include_credit             = true
    include_discount           = true
    include_other_subscription = true
    include_recurring          = true
    include_refund             = true
    include_subscription       = true
    include_support            = true
    include_tax                = true
    include_upfront            = true
    use_amortized              = false
    use_blended                = false
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
    subscriber_sns_topic_arns  = []
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
    subscriber_sns_topic_arns  = []
    threshold                  = 70
    threshold_type             = "PERCENTAGE"
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.notification_email]
    subscriber_sns_topic_arns  = []
    threshold                  = 85
    threshold_type             = "PERCENTAGE"
  }
}
