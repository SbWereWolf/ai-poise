"""Expose the maintained complete lifecycle reporter as a standard pytest plugin."""
from tests.actions.lifecycle_reporting import LifecycleReport


_report = LifecycleReport()
pytest_collection_finish = _report.pytest_collection_finish
pytest_collectreport = _report.pytest_collectreport
pytest_runtest_logreport = _report.pytest_runtest_logreport
pytest_sessionfinish = _report.pytest_sessionfinish
