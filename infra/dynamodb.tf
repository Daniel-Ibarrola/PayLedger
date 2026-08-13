locals {
  ledger_gsi1_name      = "GSI1"
  expired_hold_gsi_name = "EXPIRED_GSI"
}

resource "aws_dynamodb_table" "ledger" {
  name         = "payledger-ledger-table"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"

  attribute {
    name = "PK"
    type = "S"
  }

  attribute {
    name = "SK"
    type = "S"
  }

  attribute {
    name = "GSI1-PK"
    type = "S"
  }

  attribute {
    name = "GSI1-SK"
    type = "S"
  }

  attribute {
    name = "expires_at"
    type = "S"
  }

  global_secondary_index {
    name            = local.ledger_gsi1_name
    projection_type = "ALL"

    key_schema {
      attribute_name = "GSI1-PK"
      key_type       = "HASH"
    }

    key_schema {
      attribute_name = "GSI1-SK"
      key_type       = "RANGE"
    }
  }

  global_secondary_index {
    name            = local.expired_hold_gsi_name
    projection_type = "ALL"

    key_schema {
      attribute_name = "expires_at"
      key_type       = "HASH"
    }
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.payledger_key.arn
  }
}