"""Read-only structural audit of the skills shipped with a concrete AI-poise copy."""
import argparse
import json
from pathlib import Path
from poise.common import PoiseError
from poise.infrastructure.documentation_checks import audit_skills


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', required=True, type=Path)
    parser.add_argument('--skill', action='append')
    args=parser.parse_args()
    try:
        result=audit_skills(args.checkout,args.skill)
    except (PoiseError, OSError, ValueError) as exc:
        print(json.dumps({'status':'rejected','error':str(exc)},ensure_ascii=False));return 2
    result['status']='invalid' if result['errors'] or result['unreachable'] else 'valid'
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result['status']=='invalid' else 0

if __name__=='__main__':
    raise SystemExit(main())
