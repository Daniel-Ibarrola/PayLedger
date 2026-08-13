resource "aws_kms_key" "payledger_key" {
  description         = "KMS key for payledger encryption"
  enable_key_rotation = true
  policy              = data.aws_iam_policy_document.kms_shared_policy.json
}

resource "aws_kms_alias" "cmk_alias" {
  name          = "alias/payledger"
  target_key_id = aws_kms_key.payledger_key.key_id
}
