import { sql } from "@vercel/postgres";

export async function insertEvent(payload) {
  const meta = payload.meta || {};
  await sql`
    insert into events (
      event_name,
      session_id,
      page_path,
      occurred_at,
      accession_number,
      form_type,
      tracked_name,
      referrer_domain,
      viewport_class,
      search_length_bucket,
      user_agent_family
    ) values (
      ${payload.event_name},
      ${payload.session_id},
      ${payload.page_path},
      ${payload.occurred_at},
      ${meta.accession_number || null},
      ${meta.form_type || null},
      ${meta.tracked_name || null},
      ${meta.referrer_domain || null},
      ${meta.viewport_class || null},
      ${meta.search_length_bucket || null},
      ${meta.user_agent_family || null}
    )
  `;
}

export async function cleanupExpiredEvents(retentionDays) {
  const result = await sql`
    delete from events
    where received_at < now() - make_interval(days => ${retentionDays})
  `;
  return result.rowCount || 0;
}
