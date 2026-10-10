CREATE TABLE observed_writes(kind TEXT NOT NULL, identity TEXT NOT NULL);
CREATE TRIGGER requirement_insert AFTER INSERT ON requirements BEGIN
  INSERT INTO observed_writes(kind,identity) VALUES('requirement_insert',NEW.id);
END;
CREATE TRIGGER requirement_update AFTER UPDATE ON requirements BEGIN
  INSERT INTO observed_writes(kind,identity) VALUES('requirement_update',NEW.id);
END;
CREATE TRIGGER requirement_delete AFTER DELETE ON requirements BEGIN
  INSERT INTO observed_writes(kind,identity) VALUES('requirement_delete',OLD.id);
END;
CREATE TRIGGER link_insert AFTER INSERT ON requirement_links BEGIN
  INSERT INTO observed_writes(kind,identity)
    VALUES('link_insert',NEW.system_id || ':' || NEW.application_id);
END;
CREATE TRIGGER link_update AFTER UPDATE ON requirement_links BEGIN
  INSERT INTO observed_writes(kind,identity)
    VALUES('link_update',NEW.system_id || ':' || NEW.application_id);
END;
CREATE TRIGGER link_delete AFTER DELETE ON requirement_links BEGIN
  INSERT INTO observed_writes(kind,identity)
    VALUES('link_delete',OLD.system_id || ':' || OLD.application_id);
END;
