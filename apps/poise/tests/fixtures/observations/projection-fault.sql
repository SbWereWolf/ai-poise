-- Fire only after both novel rows actually exist inside the current connection.
-- Either real write ordering reaches this fault; earlier committed work cannot hide.
CREATE TRIGGER fixture_event_projection_fault AFTER INSERT ON observation_events
WHEN NEW.source='vendor' AND NEW.stream='transaction-stream' AND NEW.event_id='faulted'
 AND EXISTS(SELECT 1 FROM observation_streams WHERE source='vendor' AND stream='transaction-stream')
BEGIN SELECT fixture_projection_failure(); END;
CREATE TRIGGER fixture_stream_event_fault AFTER INSERT ON observation_streams
WHEN NEW.source='vendor' AND NEW.stream='transaction-stream'
 AND EXISTS(SELECT 1 FROM observation_events WHERE source='vendor' AND stream='transaction-stream' AND event_id='faulted')
BEGIN SELECT fixture_projection_failure(); END;
