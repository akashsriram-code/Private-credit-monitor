delete from events
where received_at < now() - interval '90 days';
