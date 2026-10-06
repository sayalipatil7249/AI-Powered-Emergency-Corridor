-- Admin dashboard tables (grievance and performance tracking).
-- The backend creates them on startup (Base.metadata.create_all and
-- admin_service.ensure_schema in backend/main.py); this file is for
-- pgAdmin / a fresh database. Generated from backend/models/
-- ambulance_request.py, route_optimization_log.py, grievance.py and
-- trip_event.py.

CREATE TABLE IF NOT EXISTS ambulance_requests (
	id SERIAL NOT NULL, 
	request_id VARCHAR(40) NOT NULL, 
	ambulance_id VARCHAR(50) NOT NULL, 
	start_name VARCHAR(255), 
	hospital_name VARCHAR(255), 
	status VARCHAR(20) NOT NULL, 
	traffic_level VARCHAR(10), 
	distance_meters FLOAT, 
	planned_seconds FLOAT,
	plan_status VARCHAR(20),
	dispatched_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	arrived_at TIMESTAMP WITHOUT TIME ZONE, 
	response_seconds FLOAT, 
	delay_seconds FLOAT, 
	delay_reason VARCHAR(20), 
	delay_reason_manual BOOLEAN NOT NULL, 
	stopped_seconds FLOAT, 
	stops INTEGER,
	unit_id VARCHAR(20),
	unit_kind VARCHAR(5),
	to_patient_seconds FLOAT,
	scene_seconds FLOAT,
	transport_seconds FLOAT,
	handover_seconds FLOAT,
	pre_alerted BOOLEAN,
	diverted BOOLEAN,
	source VARCHAR(20) NOT NULL,
	notes VARCHAR(500), 
	route_geometry JSON, 
	track JSON, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_ambulance_requests_delay_reason ON ambulance_requests (delay_reason);
CREATE INDEX IF NOT EXISTS ix_ambulance_requests_dispatched_at ON ambulance_requests (dispatched_at);
CREATE INDEX IF NOT EXISTS ix_ambulance_requests_id ON ambulance_requests (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_ambulance_requests_request_id ON ambulance_requests (request_id);
CREATE INDEX IF NOT EXISTS ix_ambulance_requests_status ON ambulance_requests (status);

CREATE TABLE IF NOT EXISTS route_optimization_logs (
	id SERIAL NOT NULL, 
	request_id VARCHAR(40) NOT NULL, 
	optimal_route_used BOOLEAN NOT NULL, 
	signals_total INTEGER NOT NULL, 
	signals_cleared INTEGER NOT NULL, 
	corridor_cleared BOOLEAN NOT NULL, 
	police_alerts INTEGER NOT NULL, 
	police_on_scene INTEGER NOT NULL, 
	reroutes INTEGER NOT NULL, 
	incidents INTEGER NOT NULL, 
	fast_arrival BOOLEAN NOT NULL, 
	seconds_vs_plan FLOAT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(request_id) REFERENCES ambulance_requests (request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_route_optimization_logs_id ON route_optimization_logs (id);
CREATE INDEX IF NOT EXISTS ix_route_optimization_logs_request_id ON route_optimization_logs (request_id);

CREATE TABLE IF NOT EXISTS grievances (
	id SERIAL NOT NULL, 
	ticket_no VARCHAR(20) NOT NULL, 
	request_id VARCHAR(40), 
	raised_by_role VARCHAR(20) NOT NULL, 
	raised_by_name VARCHAR(120), 
	category VARCHAR(20) NOT NULL, 
	priority VARCHAR(10) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	subject VARCHAR(200) NOT NULL, 
	description TEXT, 
	resolution_note TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	resolved_at TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_grievances_category ON grievances (category);
CREATE INDEX IF NOT EXISTS ix_grievances_id ON grievances (id);
CREATE INDEX IF NOT EXISTS ix_grievances_request_id ON grievances (request_id);
CREATE INDEX IF NOT EXISTS ix_grievances_status ON grievances (status);
CREATE UNIQUE INDEX IF NOT EXISTS ix_grievances_ticket_no ON grievances (ticket_no);

CREATE TABLE IF NOT EXISTS trip_events (
	id SERIAL NOT NULL, 
	request_id VARCHAR(40) NOT NULL, 
	seconds FLOAT NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	detail VARCHAR(500), 
	road_name VARCHAR(200), 
	junction_id VARCHAR(120), 
	junction_name VARCHAR(200), 
	junction_signal BOOLEAN, 
	latitude FLOAT, 
	longitude FLOAT, 
	duration_seconds FLOAT, 
	data JSON, 
	PRIMARY KEY (id), 
	FOREIGN KEY(request_id) REFERENCES ambulance_requests (request_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_trip_events_id ON trip_events (id);
CREATE INDEX IF NOT EXISTS ix_trip_events_junction_id ON trip_events (junction_id);
CREATE INDEX IF NOT EXISTS ix_trip_events_kind ON trip_events (kind);
CREATE INDEX IF NOT EXISTS ix_trip_events_request_id ON trip_events (request_id);

-- Upgrading a database made before the trip page existed:
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS route_geometry JSON;
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS track JSON;

-- Upgrading a database made before plan_status existed:
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS plan_status VARCHAR(20);

-- Upgrading a database made before the 108 timeline existed:
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS unit_id VARCHAR(20);
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS unit_kind VARCHAR(5);
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS to_patient_seconds FLOAT;
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS scene_seconds FLOAT;
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS transport_seconds FLOAT;
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS handover_seconds FLOAT;
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS pre_alerted BOOLEAN;
ALTER TABLE ambulance_requests ADD COLUMN IF NOT EXISTS diverted BOOLEAN;
