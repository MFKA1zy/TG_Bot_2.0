"""Avoid retaining an empty aiogram record for every visitor forever."""
from copy import copy

from aiogram.fsm.storage.memory import MemoryStorage


class PruningMemoryStorage(MemoryStorage):
    def prune(self,key):
        row=self.storage.get(key)
        if row is not None and row.state is None and not row.data:
            self.storage.pop(key,None)

    async def get_state(self,key):
        row=self.storage.get(key)
        return row.state if row else None

    async def get_data(self,key):
        row=self.storage.get(key)
        return row.data.copy() if row else {}

    async def get_value(self,storage_key,dict_key,default=None):
        row=self.storage.get(storage_key)
        return copy(row.data.get(dict_key,default) if row else default)

    async def set_state(self,key,state=None):
        await super().set_state(key,state)
        self.prune(key)

    async def set_data(self,key,data):
        await super().set_data(key,data)
        self.prune(key)
