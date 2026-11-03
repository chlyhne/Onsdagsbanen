CREATE TABLE round_captures(
    id INTEGER PRIMARY KEY,
    round_id INTEGER NOT NULL REFERENCES rounds(id),
    instant TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    request_id TEXT NOT NULL UNIQUE
);

CREATE TABLE pending_finishes(
    round_id INTEGER NOT NULL,
    boat_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    instant TEXT,
    penalty REAL NOT NULL DEFAULT 0 CHECK(penalty>=0),
    sequence INTEGER NOT NULL,
    request_id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(round_id,boat_id),
    FOREIGN KEY(round_id,boat_id) REFERENCES participations(round_id,boat_id)
);
