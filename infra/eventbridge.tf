resource "aws_iam_role" "scheduler_role" {
  name = "eventbridge-scheduler-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "scheduler.amazonaws.com" }
        Action    = "sts:AssumeRole"
      }
    ]
  })
}

resource "aws_iam_policy" "scheduler_invoke_policy" {
  name = "eventbridge-scheduler-invoke-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = module.expired_hold_sweeper.function_arn
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "scheduler_attach" {
  role       = aws_iam_role.scheduler_role.name
  policy_arn = aws_iam_policy.scheduler_invoke_policy.arn
}

resource "aws_scheduler_schedule" "expired_hold_sweeper_schedule" {
  name        = "trigger-expired-hold-sweepr-every-15-minutes"
  group_name  = "default"
  description = "Triggers the expired hold sweeper lambda function every 15 minutes"

  schedule_expression = "rate(15 minutes)"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = module.expired_hold_sweeper.function_arn
    role_arn = aws_iam_role.scheduler_role.arn

    input = jsonencode({
      action = "expired_hold_cleanup"
      source = "eventbridge_scheduler"
    })
  }
}
