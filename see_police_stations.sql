SELECT name, kind, phone FROM police_stations ORDER BY name;
SELECT station_name, road, alert_status, call_status, phone_source,
       stopped_on_arrival, stopped_after, created_at
FROM police_calls ORDER BY created_at DESC;
