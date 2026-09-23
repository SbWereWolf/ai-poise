"""requirements_database: one requirement domain, existing Poise schema owner."""
import argparse
from environment_maintenance.shared.protocol import serve,recommend
from ..shared.database import maintain
from poise.infrastructure.requirements_registry import RequirementsStore
TEMPLATES={
 'configuration_drift':'Корень базы расходится с выбранным state_root или база находится вне него. Уточните параметры либо выполните явную ревизию конфигурации; ремонт по чужому пути запрещён.',
 'missing':'Реестр требований отсутствует. Выполните infra с выбранным профилем. Исторические данные не импортируются.',
 'corrupt':'Реестр требований повреждена. Сохраните файл и восстановите подтверждённую копию; автоматическое пересоздание запрещено.',
 'version':'Реестр требований имеет несовместимую версию. Нужна явная миграция владельца либо отдельная новая установка; существующий файл не заменяется.',
 'schema':'Реестр требований: структура отличается от схемы текущего владельца. Сверьте версии поставки; автоматического DROP нет.',
 'busy':'Реестр требований: обнаружены SQLite sidecars. Завершите работающие процессы и выполните штатный checkpoint владельцем БД.',
 'unknown':'Реестр требований: проверьте исходную ошибку, конфигурацию {config} и права. Используйте отдельный профиль для новой установки.'}
def initialize(path,lock):
    RequirementsStore(path,lock,2.0,0.01)
def handle(args,request):
    if request['mode']=='recommend':return recommend(request,TEMPLATES,{'config':args.config})
    return maintain(args.config,request['mode'],lambda cfg:cfg['paths']['requirements_database'],initialize,args.state_root)
def main():
    p=argparse.ArgumentParser(description='Реестр требований Poise');p.add_argument('--config',required=True);p.add_argument('--state-root',required=True)
    return serve(p,handle)
if __name__=='__main__':raise SystemExit(main())
