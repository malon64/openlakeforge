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
    count(*) as order_count,
    sum(quantity) as units_sold,
    sum(quantity * unit_price) as gross_revenue
from sales
group by sale_date, region
