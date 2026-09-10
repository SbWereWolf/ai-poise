"""Batch accounting facade. The ledger never changes Task or Sprint state."""
from ..modules.accounting.ports import AccountingPort

class AccountingCommands:
    def __init__(self,port:AccountingPort):self.port=port
    def prepare(self,value):return self.port.prepare(value)
    def receive(self,prepared,task):self.port.receive(prepared,task)
    def begin(self,operation,task,prepared,turn_id):self.port.begin(operation,task,prepared,turn_id)
    def finish(self,operation,before,after,result):self.port.finish(operation,before,after,result)
    def close_cycle(self):self.port.close_cycle()
    def report(self,query):return self.port.report(query)
