from __future__ import annotations
import functools
from typing import Tuple

"""
Query rules:
function request syntax: query_req
function answer syntax: query, query_
important parameters:
    type: the query type
    value: the main value itself
"""

def query(typename : str, is_request : bool = False):
    def wrapped(fn):
        @functools.wraps(fn)
        @staticmethod
        def call(*args, **kwrargs) -> dict:
            res = fn(*args, **kwrargs)
            res['type'] = typename
            res['request'] = is_request
            return res
        return call
    return wrapped

class QueryMaker:
    @query('ok')
    def ok() -> dict:
        return {}
    
    @query('name')
    def name(name : str) -> dict:
        return {'value':name}
    
    @query('name')
    def name_req() -> dict:
        return {}

    @query('global_set', True)
    def global_set_req(id : str, value : str) -> dict:
        return {'id':id, 'value':value}

    @query('global_get')
    def global_get(id : str, value : str) -> dict:
        return {'id':id, 'value':value}
    
    @query('global_get', True)
    def global_get_req(id : str) -> dict:
        return {'id':id}

    @query('function', True)
    def function_req(name : str, code : str) -> dict:
        return {'id':name, 'value':code}

    @query('call', True)
    def call_req(name : str, *args, **kwargs) -> dict:
        return {'id':name, 'args':args, 'kwargs':kwargs}

    @query('call')
    def call(name : str, resource : Tuple[int, str]) -> dict: #for the call result
        return {'id':name, 'value':resource}


class QueryReader:
    def __init__(self, query : dict) -> None:
        self.__query : dict = query

    def __eq__(self, o: object) -> bool:
        if isinstance(o, str):
            return self.__query['type'] == o
        return super().__eq__(o)

    def __contains__(self, item : str) -> bool:
        return item in self.__query

    def __getitem__(self, item : str) -> str:
        return self.__query[item]

    def value(self) -> str:
        return self.__query['value']

    def id(self) -> str:
        return self.__query['id']

    def morph(self, **kwargs) -> QueryReader:
        for x in kwargs.keys():
            self.__query[x] = kwargs[x]
        return self

    def query(self) -> dict:
        return self.__query