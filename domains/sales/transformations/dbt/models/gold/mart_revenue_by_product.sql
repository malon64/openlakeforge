with sales as (
    select
        product_id,
        cast(quantity as integer) as quantity,
        cast(unit_price as double) as unit_price
    from {{ source('silver', 'sales') }}
),

products as (
    select
        product_id,
        product_name,
        category,
        cast(unit_cost as double) as unit_cost
    from {{ source('silver', 'products') }}
)

select
    products.product_id,
    products.product_name,
    products.category,
    cast(count(*) as bigint) as order_count,
    cast(sum(sales.quantity) as bigint) as units_sold,
    cast(sum(sales.quantity * sales.unit_price) as double) as gross_revenue,
    cast(sum(sales.quantity * products.unit_cost) as double) as total_cost,
    cast(sum(sales.quantity * (sales.unit_price - products.unit_cost)) as double) as gross_margin
from sales
join products using (product_id)
group by products.product_id, products.product_name, products.category
