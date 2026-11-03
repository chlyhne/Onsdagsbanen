CREATE TABLE participations(
 round_id INTEGER NOT NULL REFERENCES rounds(id),
 boat_id INTEGER NOT NULL REFERENCES boats(id),
 heat_id INTEGER REFERENCES heats(id),
 race_id INTEGER REFERENCES races(id),
 start TEXT, distance REAL, wind_category TEXT NOT NULL DEFAULT 'M',
 handicap_set_id INTEGER REFERENCES handicap_sets(id),
 PRIMARY KEY(round_id,boat_id), UNIQUE(race_id,boat_id),
 CHECK(race_id IS NULL OR (start IS NOT NULL AND distance IS NOT NULL AND handicap_set_id IS NOT NULL))
);
INSERT INTO participations(round_id,boat_id,heat_id)
 SELECT c.round_id,m.boat_id,h.id FROM v1_heat_boats m JOIN heats h ON h.id=m.heat_id JOIN round_courses c ON c.id=h.round_course_id;
INSERT OR IGNORE INTO heats(round_course_id,name,position)
 SELECT DISTINCT r.round_course_id,e.class_name,0 FROM v1_entries e JOIN races r ON r.id=e.race_id;
INSERT INTO participations(round_id,boat_id,heat_id,race_id,start,distance,wind_category,handicap_set_id)
 SELECT c.round_id,e.boat_id,h.id,e.race_id,e.start,e.distance,e.wind_category,e.handicap_set_id
 FROM v1_entries e JOIN races r ON r.id=e.race_id JOIN round_courses c ON c.id=r.round_course_id JOIN heats h ON h.round_course_id=c.id AND h.name=e.class_name WHERE 1
 ON CONFLICT(round_id,boat_id) DO UPDATE SET heat_id=excluded.heat_id,race_id=excluded.race_id,start=excluded.start,distance=excluded.distance,wind_category=excluded.wind_category,handicap_set_id=excluded.handicap_set_id;
CREATE TABLE finishes(
 race_id INTEGER NOT NULL,boat_id INTEGER NOT NULL,status TEXT NOT NULL,instant TEXT,elapsed REAL,corrected REAL,sequence INTEGER,note TEXT NOT NULL DEFAULT '',request_id TEXT NOT NULL UNIQUE,source_points REAL,source_discarded INTEGER,penalty REAL NOT NULL DEFAULT 0 CHECK(penalty>=0),auto_dnc INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(race_id,boat_id),FOREIGN KEY(race_id,boat_id) REFERENCES participations(race_id,boat_id)
);
INSERT INTO finishes SELECT race_id,boat_id,status,instant,elapsed,corrected,sequence,note,request_id,source_points,source_discarded,penalty,auto_dnc FROM v1_finishes;
DROP TABLE v1_finishes;
DROP TABLE v1_entries;
DROP TABLE v1_heat_boats;

-- These are two projections of one record, not two independently stored rosters.
CREATE VIEW heat_boats AS SELECT heat_id,boat_id FROM participations WHERE heat_id IS NOT NULL;
CREATE VIEW entries AS SELECT p.race_id,p.boat_id,h.name class_name,p.start,p.distance,p.wind_category,p.handicap_set_id FROM participations p LEFT JOIN heats h ON h.id=p.heat_id WHERE p.race_id IS NOT NULL;
CREATE TRIGGER membership_insert INSTEAD OF INSERT ON heat_boats BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM participations p JOIN heats h ON h.id=NEW.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE p.round_id=c.round_id AND p.boat_id=NEW.boat_id AND p.heat_id IS NOT NULL AND p.heat_id<>NEW.heat_id) THEN RAISE(ABORT,'En båd kan kun være i ét løb i samme ræs i serien.') END;
 INSERT INTO participations(round_id,boat_id,heat_id) SELECT c.round_id,NEW.boat_id,NEW.heat_id FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE h.id=NEW.heat_id
 ON CONFLICT(round_id,boat_id) DO UPDATE SET heat_id=excluded.heat_id;
END;
CREATE TRIGGER membership_delete INSTEAD OF DELETE ON heat_boats BEGIN
 UPDATE participations SET heat_id=NULL WHERE heat_id=OLD.heat_id AND boat_id=OLD.boat_id;
 DELETE FROM participations WHERE boat_id=OLD.boat_id AND heat_id IS NULL AND race_id IS NULL;
END;
CREATE TRIGGER membership_update INSTEAD OF UPDATE ON heat_boats BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM participations p JOIN heats h ON h.id=NEW.heat_id JOIN round_courses c ON c.id=h.round_course_id WHERE p.round_id=c.round_id AND p.boat_id=NEW.boat_id AND NOT(p.heat_id=OLD.heat_id AND p.boat_id=OLD.boat_id)) THEN RAISE(ABORT,'En båd kan kun være i ét løb i samme ræs i serien.') END;
 UPDATE participations SET boat_id=NEW.boat_id,heat_id=NEW.heat_id WHERE heat_id=OLD.heat_id AND boat_id=OLD.boat_id;
END;
CREATE TRIGGER entry_insert INSTEAD OF INSERT ON entries BEGIN
 SELECT CASE WHEN NEW.handicap_set_id IS NULL THEN RAISE(ABORT,'Deltagelsen skal referere et handicapsæt.') END;
 INSERT OR IGNORE INTO heats(round_course_id,name,position) SELECT round_course_id,NEW.class_name,0 FROM races WHERE id=NEW.race_id;
 INSERT INTO participations(round_id,boat_id,heat_id,race_id,start,distance,wind_category,handicap_set_id)
 SELECT c.round_id,NEW.boat_id,h.id,NEW.race_id,NEW.start,NEW.distance,NEW.wind_category,NEW.handicap_set_id FROM races r JOIN round_courses c ON c.id=r.round_course_id JOIN heats h ON h.round_course_id=c.id AND h.name=NEW.class_name WHERE r.id=NEW.race_id
 ON CONFLICT(round_id,boat_id) DO UPDATE SET heat_id=excluded.heat_id,race_id=excluded.race_id,start=excluded.start,distance=excluded.distance,wind_category=excluded.wind_category,handicap_set_id=excluded.handicap_set_id;
END;
CREATE TRIGGER entry_update INSTEAD OF UPDATE ON entries BEGIN
 SELECT CASE WHEN NEW.handicap_set_id IS NULL THEN RAISE(ABORT,'Deltagelsen skal referere et handicapsæt.') END;
 UPDATE participations SET heat_id=(SELECT h.id FROM races r JOIN heats h ON h.round_course_id=r.round_course_id WHERE r.id=NEW.race_id AND h.name=NEW.class_name),start=NEW.start,distance=NEW.distance,wind_category=NEW.wind_category,handicap_set_id=NEW.handicap_set_id WHERE race_id=OLD.race_id AND boat_id=OLD.boat_id;
END;
CREATE TRIGGER entry_delete INSTEAD OF DELETE ON entries BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM finishes WHERE race_id=OLD.race_id AND boat_id=OLD.boat_id) THEN RAISE(ABORT,'Gemte resultater skal bevares.') END;
 UPDATE participations SET race_id=NULL,start=NULL,distance=NULL,handicap_set_id=NULL WHERE race_id=OLD.race_id AND boat_id=OLD.boat_id;
 DELETE FROM participations WHERE boat_id=OLD.boat_id AND heat_id IS NULL AND race_id IS NULL;
END;
CREATE TRIGGER participation_owner_insert BEFORE INSERT ON participations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM boats b JOIN rounds p ON p.series_id=b.series_id WHERE b.id=NEW.boat_id AND p.id=NEW.round_id) THEN RAISE(ABORT,'Båden skal tilhøre ræsets serie.') END;
 SELECT CASE WHEN NEW.handicap_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handicap_sets h JOIN boats b ON b.profile_id=h.profile_id WHERE h.id=NEW.handicap_set_id AND b.id=NEW.boat_id) THEN RAISE(ABORT,'Handicapsættet skal høre til båden.') END;
 SELECT CASE WHEN NEW.heat_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE h.id=NEW.heat_id AND c.round_id=NEW.round_id) THEN RAISE(ABORT,'Løbet skal tilhøre ræset.') END;
END;
CREATE TRIGGER participation_owner_update BEFORE UPDATE ON participations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM boats b JOIN rounds p ON p.series_id=b.series_id WHERE b.id=NEW.boat_id AND p.id=NEW.round_id) THEN RAISE(ABORT,'Båden skal tilhøre ræsets serie.') END;
 SELECT CASE WHEN NEW.handicap_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handicap_sets h JOIN boats b ON b.profile_id=h.profile_id WHERE h.id=NEW.handicap_set_id AND b.id=NEW.boat_id) THEN RAISE(ABORT,'Handicapsættet skal høre til båden.') END;
 SELECT CASE WHEN NEW.heat_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE h.id=NEW.heat_id AND c.round_id=NEW.round_id) THEN RAISE(ABORT,'Løbet skal tilhøre ræset.') END;
END;
CREATE TRIGGER handicap_sets_immutable BEFORE UPDATE ON handicap_sets BEGIN SELECT RAISE(ABORT,'Opret et nyt handicapsæt i stedet for at ændre et brugt sæt.'); END;
CREATE TRIGGER profile_handicap_owner BEFORE UPDATE OF current_handicap_set_id ON boat_profiles WHEN NEW.current_handicap_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM handicap_sets WHERE id=NEW.current_handicap_set_id AND profile_id=NEW.id) BEGIN SELECT RAISE(ABORT,'Handicapsættet skal høre til båden.'); END;
CREATE TRIGGER boat_handicap_identity BEFORE UPDATE OF profile_id ON boats WHEN EXISTS(SELECT 1 FROM participations p JOIN handicap_sets h ON h.id=p.handicap_set_id WHERE p.boat_id=OLD.id AND h.profile_id<>NEW.profile_id) BEGIN SELECT RAISE(ABORT,'Bådens handicapsæt må ikke miste sin ejer.'); END;
CREATE TRIGGER round_uid_required BEFORE INSERT ON rounds WHEN NEW.uid IS NULL OR NEW.uid='' BEGIN SELECT RAISE(ABORT,'Ræset skal have et UID.'); END;
CREATE TRIGGER round_uid_immutable BEFORE UPDATE OF uid ON rounds WHEN NEW.uid IS NOT OLD.uid BEGIN SELECT RAISE(ABORT,'Ræsets UID kan ikke ændres.'); END;
