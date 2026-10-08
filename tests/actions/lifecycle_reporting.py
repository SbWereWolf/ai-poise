"""Standard pytest output plugin; execution and JUnit remain pytest-owned."""
import json


class LifecycleReport:
    def __init__(self):
        self.collected = []
        self.collection = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = sorted(item.nodeid for item in session.items)

    def pytest_collectreport(self, report):
        if report.failed or report.skipped:
            self.collection.append({
                "nodeid": report.nodeid, "outcome": report.outcome,
                "diagnostic": str(report.longrepr),
            })

    def pytest_runtest_logreport(self, report):
        value = {"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome}
        if report.failed:
            crash = getattr(report.longrepr, "reprcrash", None)
            value["diagnostic"] = crash.message if crash is not None else str(report.longrepr)
        if report.skipped:
            value["diagnostic"] = str(report.longrepr)
        # No phase, failed case, collection problem or skip is filtered away.
        self.reports.append(value)

    def summary(self, exit_code):
        return {"exit_code": int(exit_code), "collected": self.collected,
                "collection": self.collection,
                "reports": sorted(self.reports, key=lambda item: (item["nodeid"], item["phase"]))}

    def pytest_sessionfinish(self, session, exitstatus):
        print(json.dumps(self.summary(exitstatus), ensure_ascii=False,
                         sort_keys=True, separators=(",", ":")))
