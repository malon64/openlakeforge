with sales as (
    select
        cast(sale_date as date) as sale_date,
        region,
        cast(quantity as integer) as quantity,
        cast(unit_price as double) as unit_price
    from {{ source('silver', 'sales') }}
)

select
    sale_date,
    region,
    cast(count(*) as bigint) as order_count,
    cast(sum(quantity) as bigint) as units_sold,
    cast(sum(quantity * unit_price) as double) as gross_revenue
from sales
group by sale_date, region
