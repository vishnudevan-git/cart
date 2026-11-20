# 14 - Stock Flow Test Scenarios, Results and Open Doubts

Use this article to repeat the tests on another system. Each scenario maps to a rule in
article 13. Test files are in the project, so run them with python manage.py test.

Last run: 2026-11-20. All scenarios below pass.

## 1. Scenarios (automated, orders/test_stock_flow_e2e.py, stockist/test_blocking_and_returns.py, stockist/test_loose_blocking.py, orders/test_nearest_first.py)

| # | Scenario | Expected result | Test |
|---|---|---|---|
| S1 | Customer orders 3; stockist has 10, distributor has none | Filled from stockist (source stockist); stockist 7; supplier unchanged | 	est_stockist_stock_is_used_before_supplier |
| S2 | Customer orders 3; distributor has 5 and stockist has 10 | Filled from distributor (source wholesaler); distributor 2; stockist 10 | 	est_distributor_stock_is_used_before_stockist |
| S3 | Distributor has 5, 4 blocked; stockist has 10; order 3 | Distributor free is 1, so filled from stockist | 	est_blocked_distributor_packs_are_skipped |
| S4 | Stockist has 2; order 3 | No upper tier covers the line; supplier path (source supplier); supplier 97 | 	est_supplier_is_used_when_no_upper_tier_covers_the_line |
| S5 | Customer removes the item from a pending order | Packs return to the stockist (10) | 	est_customer_removing_the_item_returns_packs_to_the_stockist |
| S6 | Customer orders 3 from supplier stock, then reduces to 1 | Two packs return to supplier | 	est_reducing_a_supplier_item_returns_only_the_difference (stockist/test_blocking_and_returns.py) |
| S7 | Customer tries to raise quantity | Refused, message to place new order, stock untouched | 	est_increase_is_refused_and_stock_is_untouched |
| S8 | Shop requests 4 from its distributor (stock 10) | Distributor blocks 4 at once | 	est_shop_request_blocks_distributor_packs_at_once |
| S9 | Distributor fills the shop request | Shop gets 4; distributor 6; block released; request delivered | 	est_distributor_fills_shop_request_from_its_stock |
| S10 | Distributor has none and forwards to stockist | Stockist blocks 4; shop request keeps waiting | 	est_distributor_forwards_shop_request_to_stockist |
| S11 | Second shop request when free packs are short | Refused; first block unchanged | 	est_second_shop_request_is_not_blocked_when_free_packs_are_short |
| S12 | Stockist request to supplier, supplier rejects | Supplier block released | 	est_rejecting_a_request_releases_the_block |
| S13 | Stockist request to supplier, supplier ships | Supplier stock down, block released, stockist +qty | 	est_shipping_a_request_lowers_stock_and_releases_the_block |
| S14 | Stockist delivers to distributor more than it holds | Refused | 	est_delivery_refused_when_stockist_lacks_the_packs |
| S15 | Loose request, stockist grams short | Refused | 	est_block_then_refuse_when_free_grams_are_short |
| S16 | Loose shop request | Distributor grams blocked; released on cancel | 	est_shop_loose_request_blocks_distributor_grams |
| S17 | No-buffer distributor | Restock form hidden, restock action refused | 	est_restock_form_is_hidden_when_no_buffer_is_on |
| S18 | Pack with nothing free upstream | Hidden from customer page | 	est_product_with_no_stock_anywhere_is_hidden |
| S19 | Stock sheet | Blocked column present, information only | 	est_plain_pack_row_is_editable_and_highlighted |

## 2. Results

- New scenario tests: all pass.
- Wider suites: only the old failures as before (stockist: 3 errors; suppliers and products: 1 failure and 3 errors). These are not caused by the stock flow. They are listed in article 12.
- No bug was found by the end-to-end checks in this round. Every check that failed during build was fixed when found.

## 3. Open doubts (to decide with the owner)

1. **Hold, request, confirm is not built.** Nearest-first at checkout takes packs from the upper tier immediately. The owner's rule is to hold them, send a request to that tier, and wait for its confirmation. Scenario S1 to S4 show the current behaviour. Article 13 section 8 has the correction.
2. **Who sees a customer order that the stockist or distributor fills.** Checkout records the source on the item, but the order is still routed as a supplier order. The stockist or distributor may not see it in its own order list. Not checked by a test.
3. **Customer orders do not block upper-tier stock.** They take it. Shop requests block. The owner's rule says the nearest stock is blocked for every order.
4. **Supplier stock and stockist stock may hold the same packs.** Checkout reduces supplier stock only on supplier-path lines. Confirm which count is the real one when a stockist holds packs that also appear at the supplier.
5. **Increase of quantity** is refused (owner decision). Confirm this is final.
6. **Full checkout from the web pages** (cart, checkout form, customer pages) has been tested through the views, not in a browser. The owner plans manual test orders per role.

## 4. How to repeat this on another system

1. Copy the project, install requirements, create a database (python manage.py migrate).
2. Run python manage.py test orders.test_stock_flow_e2e stockist.test_blocking_and_returns stockist.test_loose_blocking orders.test_nearest_first.
3. Expect all to pass. Then run python manage.py test for the full suite. The known old failures are listed in article 12.