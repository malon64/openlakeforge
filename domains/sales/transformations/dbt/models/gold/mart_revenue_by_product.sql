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
    count(*) as order_count,
    sum(sales.quantity) as units_sold,
    sum(sales.quantity * sales.unit_price) as gross_revenue,
    sum(sales.quantity * products.unit_cost) as total_cost,
    sum(sales.quantity * (sales.unit_price - products.unit_cost)) as gross_margin
from sales
join products using (product_id)
group by products.product_id, products.product_name, products.category
