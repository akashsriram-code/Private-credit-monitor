create or replace view analytics_daily_visits as
select
  date_trunc('day', occurred_at) as day,
  count(*) filter (where event_name = 'page_view') as page_views,
  count(distinct session_id) filter (where event_name = 'page_view') as unique_sessions,
  count(distinct session_id) filter (
    where event_name in ('search_used', 'form_filter_changed', 'analysis_modal_opened', 'filing_link_clicked')
  ) as engaged_sessions
from events
group by 1
order by 1 desc;

create or replace view analytics_top_filings as
select
  accession_number,
  max(tracked_name) as tracked_name,
  max(form_type) as form_type,
  count(*) filter (where event_name = 'filing_card_viewed') as card_views,
  count(*) filter (where event_name = 'analysis_modal_opened') as modal_opens,
  count(*) filter (where event_name = 'filing_link_clicked') as filing_clicks
from events
where accession_number is not null
group by accession_number
order by filing_clicks desc, modal_opens desc, card_views desc;

create or replace view analytics_event_totals as
select
  event_name,
  count(*) as event_count
from events
group by event_name
order by event_count desc;
