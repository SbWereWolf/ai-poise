"""Forward infrastructure commands without constructing a harness session."""
import importlib.util
import json
import subprocess
import sys


def execute(argv):
    if importlib.util.find_spec('environment_maintenance') is None:
        print(json.dumps({'schema':'environment-maintenance/result/v1', 'status':'action_required',
                          'exit_code':3, 'error':'maintenance_application_missing',
                          'recommendation':'Установите environment-maintenance из wheelhouse этой поставки в выбранный Python. Конфигурация и БД Poise не изменены.'},ensure_ascii=False))
        return 3
    return subprocess.run([sys.executable,'-B','-m','environment_maintenance',*argv],check=False).returncode
