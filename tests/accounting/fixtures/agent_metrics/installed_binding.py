"""Read the actual native binding from the selected installed distribution only."""
import json
import sys

import poise
from poise.infrastructure.hook_transport import HookService

request = json.load(sys.stdin)
service = HookService(request["settings"])
binding = service.latest_binding(request["session"], "primary")
print(json.dumps({"binding": binding, "poise_source": poise.__file__}))
