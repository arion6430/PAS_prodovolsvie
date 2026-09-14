do $$
begin
    execute format('alter database %I set timezone to %L', current_database(), 'Europe/Moscow');
end
$$;
