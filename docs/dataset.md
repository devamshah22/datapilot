# Dataset — Olist Brazilian E-Commerce

Source: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
License: CC-BY-NC-SA-4.0 (non-commercial, share-alike, attribution required)

> Olist provides anonymized data from 100k orders made on the Olist Store in
> Brazil between 2016 and 2018. Used here for non-commercial educational
> purposes per the license terms.

## Why this dataset

- Real-world e-commerce schema with 9 tables and foreign-key relationships
- ~100k orders — large enough for realistic queries, small enough that
  DuckDB can fully process locally in well under a second
- Multilingual category names (Portuguese), with a translation table —
  natural source of agent challenges
- Includes reviews, payments, geolocation, and seller data — supports a
  wide variety of analytical questions
- Public and well-known on Kaggle, so anyone reviewing the project can
  verify what the agent is operating against

## Files (what's in `data/`)

> The raw CSVs are gitignored — re-fetch with `scripts/load_olist.py`
> after placing your Kaggle API token at `~/.kaggle/kaggle.json`.

| File                                   | Rows      | Notes                                      |
| -------------------------------------- | --------- | ------------------------------------------ |
| `olist_orders_dataset.csv`             | 99,441    | One row per order                          |
| `olist_order_items_dataset.csv`        | 112,650   | One row per item-in-order; FK to orders    |
| `olist_order_payments_dataset.csv`     | 103,886   | Payment installments per order             |
| `olist_order_reviews_dataset.csv`      | 99,224    | Customer reviews; 1-5 score                |
| `olist_customers_dataset.csv`          | 99,441    | Customer records                           |
| `olist_products_dataset.csv`           | 32,951    | Product catalog                            |
| `olist_sellers_dataset.csv`            | 3,095     | Seller catalog                             |
| `olist_geolocation_dataset.csv`        | 1,000,163 | Brazilian zip-code → lat/long              |
| `product_category_name_translation.csv`| 71        | Portuguese → English category names        |

## v1 flat table — `olist_v1_flat.csv`

The agent in early weeks operates against a single denormalized CSV produced
by `scripts/load_olist.py`. It joins orders + items + products + customers
+ reviews + the category translation into one 25-column flat file
(112,650 rows, ~41 MB).

This keeps week 2-4 focused on **agent capability** without forcing the
agent to also figure out joins. Multi-table reasoning is unlocked as a v2
feature in week 5-6.

### v1 schema

```
order_id                          customer_id
customer_unique_id                customer_city
customer_state                    order_status
order_purchase_timestamp          order_approved_at
order_delivered_carrier_date      order_delivered_customer_date
order_estimated_delivery_date     order_item_id
product_id                        seller_id
shipping_limit_date               price
freight_value                     product_category_name        (Portuguese)
product_category_en               product_weight_g
product_length_cm                 product_height_cm
product_width_cm                  review_score                 (1-5, nullable)
review_creation_date
```

## Reproducing the dataset locally

```powershell
# 1. Place Kaggle API token
# Get it from kaggle.com -> Settings -> API -> Create Legacy API Token
# Move kaggle.json to ~/.kaggle/kaggle.json

# 2. Download
.\.venv\Scripts\kaggle.exe datasets download -d olistbr/brazilian-ecommerce -p data/ --unzip

# 3. Build the flat table
.\.venv\Scripts\python.exe scripts\load_olist.py
```

## Attribution

When demoing or presenting DataPilot, credit:
> Dataset: Olist Brazilian E-Commerce Public Dataset (Kaggle, CC-BY-NC-SA-4.0)
