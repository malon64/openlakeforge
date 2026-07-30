with spend as (

    select
        s.spend_date,
        c.channel,
        s.campaign_id,
        s.impressions,
        s.clicks,
        s.spend_amount
    from {{ source('silver', 'ad_spend') }} as s
    inner join {{ source('silver', 'campaigns') }} as c
        on s.campaign_id = c.campaign_id

)

select
    spend_date,
    channel,
    cast(count(distinct campaign_id) as integer) as campaign_count,
    cast(sum(impressions) as bigint) as impressions,
    cast(sum(clicks) as bigint) as clicks,
    cast(sum(spend_amount) as double) as spend_amount,
    case
        when sum(impressions) = 0 then cast(0 as double)
        else cast(sum(clicks) as double) / cast(sum(impressions) as double)
    end as click_through_rate
from spend
group by spend_date, channel
