"""Format explicitly selected AI-poise Markdown only when IDE formatting is unavailable."""
import argparse
import json
from pathlib import Path
import sys
from poise.common import PoiseError
from poise.infrastructure.markdown_format import format_files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout',required=True,type=Path)
    parser.add_argument('--file',action='append',default=[])
    parser.add_argument('--files',nargs='+',default=[])
    parser.add_argument('--files-from',action='append',default=[])
    parser.add_argument('--len',dest='width',type=int,required=True)
    parser.add_argument('--eol',choices=['LF','CR','CRLF'],required=True)
    parser.add_argument('--lint-mode',choices=['check','fix'],required=True)
    parser.add_argument('--fallback-reason',required=True)
    args=parser.parse_args()
    try:
        files=args.file+args.files
        for source in args.files_from:
            text=sys.stdin.read() if source=='-' else Path(source).read_text(encoding='utf-8')
            files.extend(line.strip() for line in text.splitlines() if line.strip())
        result=format_files(args.checkout,files,args.width,args.eol,args.lint_mode,args.fallback_reason)
        code=1 if result['status']=='drift' else 0
    except (PoiseError,OSError,ValueError) as exc:
        result,code={'status':'rejected','reason':str(exc)},2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return code


if __name__=='__main__':raise SystemExit(main())
