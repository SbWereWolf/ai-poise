import os
from pathlib import Path

counter = Path(os.environ['RECEIPT_COUNTER'])
with counter.open('a', encoding='utf-8') as stream:
    stream.write('executed\n')
print('receipt probe')
if Path('reject-integration').exists():
    raise SystemExit(7)
