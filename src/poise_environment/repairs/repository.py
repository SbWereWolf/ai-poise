"""Local repository readiness; never initialize, reset, fetch or publish implicitly."""
import argparse
import os
from pathlib import Path
from environment_maintenance.process import run
from environment_maintenance.shared.filesystem import lexical_path
from environment_maintenance.shared.protocol import serve, result, recommend

TEMPLATES = {
    'missing': 'Репозиторий {repository} отсутствует или недоступен. Укажите существующий Git-каталог через --set repository; автоматического clone/init нет.',
    'root': 'Путь {repository} не является корнем рабочего дерева. Укажите точный корень, не вложенный каталог.',
    'base': 'В локальном репозитории {repository} недоступна базовая ревизия. Укажите существующую base_ref или отдельно подготовьте репозиторий.',
    'unknown': 'Проверьте путь {repository}, выбранный Git и исходную диагностику. Автоматическое изменение веток, сети и содержимого запрещено.'
}


def handle(args, request):
    if request['mode'] == 'recommend':
        return recommend(request, TEMPLATES, {'repository': args.repository})
    repository = lexical_path(args.repository)
    if not repository.is_dir() or not os.access(repository, os.R_OK | os.X_OK):
        return result('action_required', 'missing')
    def probe(*arguments):
        return run({'argv': [args.git, '-C', str(repository), *arguments], 'timeout': 10},
                   cwd=repository, environ=dict(os.environ))
    top = probe('rev-parse', '--show-toplevel')
    if top['status'] != 'passed' or Path(top['stdout'].strip()) != repository:
        return result('action_required', 'root', diagnostic=top)
    revision = probe('rev-parse', '--verify', '--end-of-options', args.base_ref + '^{commit}')
    if revision['status'] != 'passed':
        return result('action_required', 'base', diagnostic=revision)
    return result('satisfied', 'ok', base_commit=revision['stdout'].strip())


def main():
    parser = argparse.ArgumentParser(description='Готовность локального репозитория Poise')
    parser.add_argument('--repository', required=True)
    parser.add_argument('--base-ref', required=True)
    parser.add_argument('--git', default='git')
    return serve(parser, handle)


if __name__ == '__main__':
    raise SystemExit(main())
