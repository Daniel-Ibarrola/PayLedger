resource "aws_budgets_budget" "daily_cost_budget" {
  name              = "${local.name_prefix}-daily-cost-budget"
  budget_type       = "COST"
  limit_amount      = "20.0"
  limit_unit        = "USD"
  time_unit         = "DAILY"

  # Alarm 1: Sends email if ACTUAL costs exceed 80% of budget
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["daniel.ibarrola.sanchez@gmail.com"]
  }

  # Alarm 2: Sends email if FORECASTED costs exceed 100% of budget ($100)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = ["daniel.ibarrola.sanchez@gmail.com"]
  }
}
