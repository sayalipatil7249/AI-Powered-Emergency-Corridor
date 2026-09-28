CREATE EXTENSION postgis;

-- verify postgis location point
SELECT
    hospital_id,
    name,
    latitude,
    longitude,
    ST_AsText(location) AS location_point
FROM hospitals
WHERE hospital_id = 'H001';


SELECT
    hospital_id,
    name,
    latitude,
    longitude,
    ST_AsText(location) AS location_point
FROM hospitals
WHERE hospital_id = 'H002';

SELECT
    hospital_id,
    name,
    latitude,
    longitude,
    ST_AsText(location) AS location_point
FROM hospitals
WHERE hospital_id = 'H003';

-- verify distance calculation
SELECT
    a.ambulance_id,
    a.latitude AS ambulance_latitude,
    a.longitude AS ambulance_longitude,
    h.hospital_id,
    h.name AS hospital_name,
    h.latitude AS hospital_latitude,
    h.longitude AS hospital_longitude,
    ST_Distance(
        a.location::geography,
        h.location::geography
    ) AS distance_meters
FROM ambulances a
CROSS JOIN hospitals h
WHERE a.ambulance_id = 'A103'
  AND h.hospital_id = 'H001';


SELECT
    a.ambulance_id,
    h.hospital_id,
    h.name AS hospital_name,
    ROUND(
        ST_Distance(
            a.location::geography,
            h.location::geography
        )::numeric,
        2
    ) AS distance_meters
FROM ambulances a
CROSS JOIN hospitals h
WHERE a.ambulance_id = 'A103'
ORDER BY distance_meters ASC;


SELECT
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'emergencies'
ORDER BY ordinal_position;


SELECT
    id,
    emergency_id,
    ambulance_id,
    hospital_id,
    status,
    created_at,
    completed_at
FROM emergencies
ORDER BY id DESC;


ALTER TABLE emergencies
ADD COLUMN route_geometry JSON,
ADD COLUMN route_distance_meters FLOAT,
ADD COLUMN route_duration_seconds FLOAT,
ADD COLUMN route_junctions JSON;

SELECT
    column_name,
    data_type
FROM information_schema.columns
WHERE table_name = 'emergencies'
ORDER BY ordinal_position;



SELECT
    emergency_id,
    ambulance_id,
    hospital_id,
    status,
    route_distance_meters,
    route_duration_seconds,
    route_geometry IS NOT NULL AS geometry_saved,
    route_junctions IS NOT NULL AS junctions_saved
FROM emergencies
WHERE emergency_id = 'E-A103';



SELECT
    emergency_id,
    ambulance_id,
    status
FROM emergencies
ORDER BY id;