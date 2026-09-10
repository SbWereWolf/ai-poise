"""Optional exact payload measurement command. Caller chooses installed version and encoding."""
import argparse
from importlib.metadata import version
import json
import sys


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--package-version',required=True)
    p.add_argument('--encoding',required=True)
    a=p.parse_args()
    if version('tiktoken')!=a.package_version:raise RuntimeError('Explicit tiktoken version does not match installed package')
    import tiktoken
    encoding=tiktoken.get_encoding(a.encoding)
    data=json.load(sys.stdin)
    if not isinstance(data,dict) or set(data)!={'texts'} or not isinstance(data['texts'],list) or any(not isinstance(t,str) for t in data['texts']):
        raise ValueError('Expected an explicit list of texts')
    json.dump({'counts':[len(encoding.encode_ordinary(t)) for t in data['texts']]},sys.stdout)

if __name__=='__main__':main()
