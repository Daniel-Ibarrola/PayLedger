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

  global_secondary_index {
    name            = "GSI1"
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

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}