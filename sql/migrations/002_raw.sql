create table raw.payload (
    payload_id     bigint generated always as identity primary key,
    load_id        bigint not null references meta.load_log,
    source         text   not null references meta.source,
    entity         text   not null,
    request_url    text   not null,
    request_params jsonb,
    http_status    int    not null,
    content_type   text,
    byte_size      bigint not null,
    sha256         char(64) not null,
    body_gz        bytea  not null,
    fetched_at     timestamptz not null default now()
);

alter table meta.load_log
    add constraint load_log_payload_fk foreign key (payload_id) references raw.payload;

create table raw.cbr_currency (
    currency_id      text primary key,
    name             text,
    eng_name         text,
    nominal          int,
    parent_code      text,
    iso_num_code     text,
    iso_char_code    text,
    first_payload_id bigint not null references raw.payload,
    last_payload_id  bigint not null references raw.payload,
    loaded_at        timestamptz not null default now(),
    updated_at       timestamptz
);

create table raw.cbr_rate (
    currency_id      text not null,
    rate_date        date not null,
    nominal          int,
    value            numeric,
    vunit_rate       numeric,
    first_payload_id bigint not null references raw.payload,
    last_payload_id  bigint not null references raw.payload,
    loaded_at        timestamptz not null default now(),
    updated_at       timestamptz,
    primary key (currency_id, rate_date)
);

create table raw.fedstat_obs (
    okato_code       text not null,
    product_code     text not null,
    obs_year         int  not null,
    period_label     text not null,
    week_no          int,
    value            numeric,
    value_raw        text,
    unit             text,
    first_payload_id bigint not null references raw.payload,
    last_payload_id  bigint not null references raw.payload,
    loaded_at        timestamptz not null default now(),
    updated_at       timestamptz,
    primary key (okato_code, product_code, obs_year, period_label)
);

create table raw.fedstat_code (
    dim              text not null,
    code             text not null,
    name             text not null,
    first_payload_id bigint not null references raw.payload,
    last_payload_id  bigint not null references raw.payload,
    loaded_at        timestamptz not null default now(),
    updated_at       timestamptz,
    primary key (dim, code)
);

create table raw.revision (
    revision_id bigint generated always as identity primary key,
    table_name  text  not null,
    record_key  jsonb not null,
    old_values  jsonb not null,
    new_values  jsonb not null,
    payload_id  bigint not null references raw.payload,
    detected_at timestamptz not null default now()
);
