# The toy table. This is NOT the ledger's single-table design — it is a
# placeholder with one string key, deliberately shaped nothing like the real
# thing so it can be deleted outright rather than migrated.

resource "aws_dynamodb_table" "toy_items" {
  name         = "${local.name_prefix}-toy-items"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "item_id"

  attribute {
    name = "item_id"
    type = "S"
  }

  # Off for the toy table: it holds no data worth recovering and PITR is billed
  # per GB of table size. It goes on for the real ledger table.
  point_in_time_recovery {
    enabled = false
  }
}
