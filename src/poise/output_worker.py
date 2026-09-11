"""Internal asynchronous view worker. Never invokes the original project command."""
import sys
from .infrastructure.result_views import materialize_job
if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('exactly one captured job path required')
    raise SystemExit(materialize_job(sys.argv[1]))
