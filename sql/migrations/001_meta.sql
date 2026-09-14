create schema if not exists meta;
create schema if not exists raw;

create table meta.source (
    source_code   text primary key,
    name          text not null,
    provider      text not null,
    url           text not null,
    access_method text not null,
    data_format   text not null,
    frequency     text not null,
    notes         text
);

insert into meta.source values
('cbr', 'Официальные курсы иностранных валют', 'Банк России',
 'https://www.cbr.ru/scripts/XML_dynamic.asp',
 'HTTP GET, без авторизации', 'XML (windows-1251)', 'ежедневно по рабочим дням',
 'Банк России просит обращаться к сервису не чаще одного раза в сутки'),
('fedstat', 'Еженедельные средние потребительские цены (показатель 37426)', 'Росстат / ЕМИСС',
 'https://www.fedstat.ru/indicator/37426',
 'HTTP GET страницы показателя + POST формы выгрузки с сессионным токеном', 'SDMX-ML 1.0 Generic (XML, utf-8)',
 'еженедельно', 'Разрезы: вид товара x территория (ОКАТО) x год x неделя');

create table meta.etl_run (
    run_id        bigint generated always as identity primary key,
    command       text not null,
    args          jsonb,
    git_commit    text,
    host          text,
    started_at    timestamptz not null default now(),
    finished_at   timestamptz,
    status        text not null default 'running'
                  check (status in ('running', 'success', 'partial', 'failed')),
    steps_success int,
    steps_skipped int,
    steps_failed  int
);

create table meta.load_log (
    load_id        bigint generated always as identity primary key,
    run_id         bigint not null references meta.etl_run,
    source         text   not null references meta.source,
    entity         text   not null,
    params         jsonb,
    status         text   not null default 'running'
                   check (status in ('running', 'success', 'skipped', 'failed', 'aborted')),
    started_at     timestamptz not null default now(),
    finished_at    timestamptz,
    http_status    int,
    attempts       int,
    byte_size      bigint,
    payload_id     bigint,
    rows_parsed    int not null default 0,
    rows_inserted  int not null default 0,
    rows_updated   int not null default 0,
    rows_unchanged int not null default 0,
    note           text,
    error_message  text
);

create index load_log_lookup_idx on meta.load_log (source, entity, status, finished_at desc);
