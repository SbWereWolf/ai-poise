from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_route_migration_keeps_storage_out_of_domain_and_application():
    domain = ROOT / "src/poise/modules/workflow/migration.py"
    port = ROOT / "src/poise/modules/workflow/ports.py"
    application = ROOT / "src/poise/application/route_migration.py"
    infrastructure = ROOT / "src/poise/infrastructure/route_migration.py"
    interface = ROOT / "src/poise/interfaces/route_migration.py"

    assert all(path.is_file() for path in (domain, port, application, infrastructure, interface))
    for path in (domain, port, application):
        source = path.read_text(encoding="utf-8")
        assert "sqlite3" not in source
        assert "Path(" not in source
