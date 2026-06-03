with sales as (
    select
        customer_id,
        cast(quantity as integer) as quantity,
        cast(unit_price as double) as unit_price
    from {{ source('silver', 'sales') }}
),

customers as (
    select
        customer_id,
        first_name,
        last_name,
        country,
        segment
    from {{ source('silver', 'customers') }}
)

select
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    customers.country,
    customers.segment,
    cast(count(*) as bigint) as order_count,
    cast(sum(sales.quantity) as bigint) as units_sold,
    cast(sum(sales.quantity * sales.unit_price) as double) as gross_revenue
from sales
join customers using (customer_id)
group by
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    customers.country,
    customers.segment
