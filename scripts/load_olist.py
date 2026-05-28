"""Smoke test for the Olist dataset.

What this does:
  1. Loads each Olist CSV into DuckDB and reports row counts.
  2. Runs a small sanity query (one cross-table join).
  3. Builds a denormalized v1 table joining orders + items + products
     + customers + reviews and saves it as `olist_v1_flat.csv`.

The flat CSV is what the agent will work against in early weeks before
multi-table reasoning is unlocked.

Run from project root:
    .\.venv\Scripts\python.exe scripts\load_olist.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

CSV_FILES = {
    "customers":   "olist_customers_dataset.csv",
    "orders":      "olist_orders_dataset.csv",
    "items":       "olist_order_items_dataset.csv",
    "payments":    "olist_order_payments_dataset.csv",
    "reviews":     "olist_order_reviews_dataset.csv",
    "products":    "olist_products_dataset.csv",
    "sellers":     "olist_sellers_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}


def main() -> int:
    con = duckdb.connect(":memory:")

    print("Loading tables into DuckDB:")
    for table, fname in CSV_FILES.items():
        path = DATA / fname
        if not path.exists():
            print(f"  MISSING: {path}")
            return 2
        # read_csv_auto handles type inference; we keep tables as views over the CSVs
        con.execute(
            f"CREATE OR REPLACE TABLE {table} AS "
            f"SELECT * FROM read_csv_auto('{path.as_posix()}')"
        )
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:22s} {n:>10,} rows")

    # Sanity: a small cross-table query
    print("\nSanity check — top 5 product categories by order count:")
    rows = con.execute(
        """
        SELECT
            COALESCE(t.product_category_name_english, p.product_category_name) AS category,
            COUNT(*) AS n_items
        FROM items i
        JOIN products p USING (product_id)
        LEFT JOIN category_translation t USING (product_category_name)
        GROUP BY 1
        ORDER BY n_items DESC
        LIMIT 5
        """
    ).fetchall()
    for cat, n in rows:
        print(f"  {cat:35s} {n:>8,}")

    # Build denormalized v1 flat table
    print("\nBuilding denormalized v1 table (orders + items + products + customers + reviews)...")
    t0 = time.perf_counter()
    con.execute(
        """
        CREATE OR REPLACE TABLE flat AS
        SELECT
            o.order_id,
            o.customer_id,
            c.customer_unique_id,
            c.customer_city,
            c.customer_state,
            o.order_status,
            o.order_purchase_timestamp,
            o.order_approved_at,
            o.order_delivered_carrier_date,
            o.order_delivered_customer_date,
            o.order_estimated_delivery_date,
            i.order_item_id,
            i.product_id,
            i.seller_id,
            i.shipping_limit_date,
            i.price,
            i.freight_value,
            p.product_category_name,
            COALESCE(ct.product_category_name_english, p.product_category_name) AS product_category_en,
            p.product_weight_g,
            p.product_length_cm,
            p.product_height_cm,
            p.product_width_cm,
            r.review_score,
            r.review_creation_date
        FROM orders o
        JOIN items i              ON i.order_id = o.order_id
        JOIN products p           ON p.product_id = i.product_id
        JOIN customers c          ON c.customer_id = o.customer_id
        LEFT JOIN category_translation ct ON ct.product_category_name = p.product_category_name
        LEFT JOIN (
            -- one review per order (latest by creation_date if multiple)
            SELECT order_id, review_score, review_creation_date,
                   ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_creation_date DESC) AS rn
            FROM reviews
        ) r ON r.order_id = o.order_id AND r.rn = 1
        """
    )
    n_flat = con.execute("SELECT COUNT(*) FROM flat").fetchone()[0]
    n_cols = len(con.execute("DESCRIBE flat").fetchall())
    print(f"  flat: {n_flat:,} rows x {n_cols} columns  ({time.perf_counter() - t0:.2f}s)")

    out_csv = DATA / "olist_v1_flat.csv"
    # Explicit ISO timestamp/date formats so the resulting CSV round-trips
    # cleanly when reloaded with TIMESTAMP type overrides.
    con.execute(
        f"COPY flat TO '{out_csv.as_posix()}' "
        "(HEADER, DELIMITER ',', "
        "TIMESTAMPFORMAT '%Y-%m-%d %H:%M:%S', "
        "DATEFORMAT '%Y-%m-%d')"
    )
    size_mb = out_csv.stat().st_size / (1024 * 1024)
    print(f"  wrote {out_csv.name}  ({size_mb:.1f} MB)")

    # Quick query on the flat file to confirm it works end-to-end
    print("\nVerifying flat file is queryable...")
    con2 = duckdb.connect(":memory:")
    con2.execute(f"CREATE TABLE f AS SELECT * FROM read_csv_auto('{out_csv.as_posix()}')")
    avg_review = con2.execute(
        "SELECT product_category_en, AVG(review_score) AS avg_score, COUNT(*) AS n "
        "FROM f WHERE review_score IS NOT NULL "
        "GROUP BY 1 ORDER BY n DESC LIMIT 3"
    ).fetchall()
    print("  Top 3 categories by item count, with avg review score:")
    for cat, avg, n in avg_review:
        print(f"    {cat:35s} avg_score={avg:.2f}  n={n:,}")

    print("\nOK: Olist data loaded, joined, and flat file written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
