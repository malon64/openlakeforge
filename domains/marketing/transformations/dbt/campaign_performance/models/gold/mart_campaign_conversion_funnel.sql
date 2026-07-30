select
    c.campaign_id,
    c.campaign_name,
    c.channel,
    cast(coalesce(sum(case when v.conversion_type = 'signup' then v.conversion_count end), 0) as bigint)
        as signup_count,
    cast(coalesce(sum(case when v.conversion_type = 'purchase' then v.conversion_count end), 0) as bigint)
        as purchase_count,
    cast(coalesce(sum(v.revenue_amount), 0) as double) as revenue_amount
from {{ source('silver', 'campaigns') }} as c
left join {{ source('silver', 'conversions') }} as v
    on c.campaign_id = v.campaign_id
group by c.campaign_id, c.campaign_name, c.channel
