with spend as (

    select campaign_id, sum(spend_amount) as spend_amount
    from {{ source('silver', 'ad_spend') }}
    group by campaign_id

),

revenue as (

    select campaign_id, sum(revenue_amount) as revenue_amount
    from {{ source('silver', 'conversions') }}
    group by campaign_id

)

select
    c.campaign_id,
    c.campaign_name,
    c.channel,
    cast(coalesce(s.spend_amount, 0) as double) as spend_amount,
    cast(coalesce(r.revenue_amount, 0) as double) as revenue_amount,
    case
        when coalesce(s.spend_amount, 0) = 0 then cast(0 as double)
        else cast(coalesce(r.revenue_amount, 0) as double) / cast(s.spend_amount as double)
    end as return_on_ad_spend
from {{ source('silver', 'campaigns') }} as c
left join spend as s on c.campaign_id = s.campaign_id
left join revenue as r on c.campaign_id = r.campaign_id
