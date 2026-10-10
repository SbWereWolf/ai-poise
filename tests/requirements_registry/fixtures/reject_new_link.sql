CREATE TRIGGER reject_new_link BEFORE INSERT ON requirement_links
WHEN NEW.application_id = 'OTHER' BEGIN
  SELECT RAISE(ABORT, 'test link refusal');
END;
