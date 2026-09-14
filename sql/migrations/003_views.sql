create view meta.v_load_log as
select l.load_id,
       l.run_id,
       l.source,
       l.entity,
       l.status,
       to_char(l.started_at, 'YYYY-MM-DD HH24:MI:SS')                    as started,
       round(extract(epoch from l.finished_at - l.started_at)::numeric, 1) as sec,
       l.http_status,
       l.attempts,
       l.byte_size,
       l.rows_parsed,
       l.rows_inserted,
       l.rows_updated,
       l.rows_unchanged,
       l.params,
       coalesce(l.error_message, l.note)                                  as message
from meta.load_log l;

create view meta.v_run as
select r.run_id,
       r.command,
       r.args ->> 'source'                                                 as source,
       r.status,
       to_char(r.started_at, 'YYYY-MM-DD HH24:MI:SS')                      as started,
       round(extract(epoch from r.finished_at - r.started_at)::numeric, 1) as sec,
       r.steps_success,
       r.steps_skipped,
       r.steps_failed,
       (select sum(rows_inserted) from meta.load_log l where l.run_id = r.run_id) as rows_inserted,
       (select sum(rows_updated) from meta.load_log l where l.run_id = r.run_id)  as rows_updated,
       r.git_commit
from meta.etl_run r;

create view meta.v_freshness as
select 'cbr'                                          as source,
       coalesce(c.iso_char_code, r.currency_id)        as dataset,
       count(*)                                        as rows,
       1                                               as regions,
       min(r.rate_date)::text                          as period_from,
       max(r.rate_date)::text                          as period_to,
       max(coalesce(r.updated_at, r.loaded_at))        as last_change
from raw.cbr_rate r
         left join raw.cbr_currency c using (currency_id)
group by 1, 2
union all
select 'fedstat',
       coalesce(n.name, o.product_code),
       count(*),
       count(distinct o.okato_code),
       min(o.obs_year * 100 + o.week_no) / 100 || '-W' || lpad((min(o.obs_year * 100 + o.week_no) % 100)::text, 2, '0'),
       max(o.obs_year * 100 + o.week_no) / 100 || '-W' || lpad((max(o.obs_year * 100 + o.week_no) % 100)::text, 2, '0'),
       max(coalesce(o.updated_at, o.loaded_at))
from raw.fedstat_obs o
         left join raw.fedstat_code n on n.dim = 's_grtov' and n.code = o.product_code
group by 1, 2;
