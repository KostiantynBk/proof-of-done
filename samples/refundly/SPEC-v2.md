# Refundly Refund Policy & Service Requirements v2.0

The November 2026 policy update clarifies refund controls following an internal payment review. Amounts are denominated in the order currency. A refund is issued only when the gateway accepts it.

## Requirement R-A
A refund may never exceed the captured amount minus refunds already issued.

## Requirement R-B
Refunds requested more than 90 days after the order date require manager approval.

## Requirement R-C
Every issued refund must create an audit record containing actor, amount, reason and order id.

## Requirement R-D
A refund must be returned to the original payment method; requests to a different method are rejected, except for the gift card policy below.

## Requirement R-E
Orders paid by gift card are refunded only as store credit.

## Requirement R-F
Refund requests are idempotent: the same request_id processed twice results in exactly one refund.

## Requirement R-G
Gateway refund calls are retried at most 3 times; after the third failure the refund is marked FAILED and no money moves.

## Requirement R-H
Partial refund amounts are rounded down to the cent.
