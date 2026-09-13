"""Batch accounting facade. The ledger never changes Task or Sprint state."""
from ..modules.accounting.ports import AccountingPort

class AccountingCommands:
    def __init__(self,port:AccountingPort):self.port=port
    def report(self,query):return self.port.report(query)
    def telemetry_summary(self,runtime):return self.port.telemetry_summary(runtime)
