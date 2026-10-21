CREATE TEMP TRIGGER interrupt_second_record
BEFORE INSERT ON execution_records
WHEN NEW.record_id = 'forced-failure'
BEGIN
  SELECT RAISE(ABORT, 'test-owned batch failure');
END;
