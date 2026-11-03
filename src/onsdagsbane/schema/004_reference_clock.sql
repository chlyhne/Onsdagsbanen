CREATE TABLE round_clocks(
    round_id INTEGER PRIMARY KEY REFERENCES rounds(id),
    offset REAL NOT NULL DEFAULT 0
);
CREATE TABLE clock_sources(
    round_id INTEGER NOT NULL, boat_id INTEGER NOT NULL,
    PRIMARY KEY(round_id,boat_id),
    FOREIGN KEY(round_id,boat_id) REFERENCES participations(round_id,boat_id)
);
CREATE TABLE clock_capture_sources(
    capture_id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES rounds(id)
);
-- Earlier UI click-times retained subsecond precision; manually typed times
-- were always whole seconds. External imported race data stays untouched.
INSERT INTO clock_sources
    SELECT c.round_id,f.boat_id FROM finishes f JOIN races r ON r.id=f.race_id
    JOIN round_courses c ON c.id=r.round_course_id
    WHERE r.source IS NULL AND substr(f.instant,20,1)='.';
INSERT OR IGNORE INTO clock_sources
    SELECT round_id,boat_id FROM pending_finishes WHERE substr(instant,20,1)='.';
INSERT INTO clock_capture_sources
    SELECT x.id,c.round_id FROM captures x JOIN races r ON r.id=x.race_id
    JOIN round_courses c ON c.id=r.round_course_id
    WHERE r.source IS NULL AND substr(x.instant,20,1)='.';
INSERT INTO clock_capture_sources
    SELECT -id,round_id FROM round_captures WHERE substr(instant,20,1)='.';
