from __future__ import annotations
from abc import ABCMeta, abstractmethod
from threading import Thread
from time import sleep
from typing import Any, Callable, List, Set, Tuple, Union
from queue import Queue

from ..environment.logger import Logger
from .function_wrapper import FunctionWrapper
from .resource import Resource
from ..data_processing import QueryMaker, QueryReader, eval_from_query, get_value_query
from ..network.protocol import ISocket, protocol
from ..network.client_manager import ClientManager
from ..network.utils import netMap, ip

class IBox(metaclass=ABCMeta):
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def on(self) -> bool:
        pass

    @abstractmethod
    def overload(self) -> int:
        pass

    @abstractmethod
    def call(self, name: str, *args, **kwargs) -> Any:
        pass

    @abstractmethod
    def callasync(self, name: str, *args, **kwargs) -> Resource:
        pass

    @abstractmethod
    def resource(self, id : int, delete : bool) -> Any:
        pass

    @abstractmethod
    def __setitem__(self, k: str, v: Any) -> None:
        pass

    @abstractmethod
    def __getitem__(self, k: str) -> str:
        pass

    def __hash__(self) -> int:
        return hash(self.name()) #hash only the name

    #if the names are equals they are the same besides maybe they are not
    #because on the network can not be two boxes with the same name
    def __eq__(self, o: object) -> bool:
        if not isinstance(o, Box): return False
        return self.name() == o.name()

def shared(use_self : bool = False):
    def shared_obj(obj : Any) -> Any: #Allows properties be accessed from the outside
        obj.__is_shared__ = True
        obj.__use_self__ = use_self
        return obj
    return shared_obj

class MetaBox(ABCMeta):
    def __call__(cls, *args, **kwargs):
        #Includes all the shared elements into the shared methods dictionary
        cls.shared_methods = {}
        for id in dir(cls):
            attr = getattr(cls, id)
            if hasattr(attr, '__is_shared__') and attr.__is_shared__:
                cls.shared_methods[id] = attr
        return super().__call__(*args, **kwargs)

class Box(IBox, ClientManager, metaclass=MetaBox):
    def __init__(self) -> None:
        self.connections : dict[str, ISocket] = {}
        self.envGlobals : dict[str, Any] = self.shared_methods
        self.idcounter : int = 0
        self.resources : dict[int, Tuple[Thread, Queue]] = {}
        super().__init__("%s::%s" % (type(self).__name__, self.name()))

    def call(self, name: str, *args, **kwargs) -> Any:
        try:
            fn = self.envGlobals[name]
            use_self = hasattr(fn, '__use_self__') and fn.__use_self__
            return fn(*args, **kwargs) if not use_self else fn(self, *args, **kwargs)
        except Exception as e:
            return e

    def callasync(self, name: str, *args, **kwargs) -> Resource:
        queue = Queue()
        id = self.idcounter
        t = Thread(target=lambda q: q.put(self.call(name, *args, **kwargs)), args=(queue,))
        self.resources[id] = (t, queue)
        self.idcounter += 1
        t.start()
        return Resource(id, self)

    def resource(self, id : int, delete : bool) -> Any:
        time = 0.0001
        if id in self.resources:
            while self.resources[id][1].qsize() == 0:
                sleep(time)
                time*=2
            val = self.resources[id][1].get()
            if delete:
                del self.resources[id]
            return val
        return None

    def __setitem__(self, k: str, v: Any) -> None:
        self.envGlobals[k] = v

    def __getitem__(self, k: str) -> str:
        return self.envGlobals[k]

    def managerMessage(self, message: dict, sck: ISocket):
        query = QueryReader(message)
        if query == 'name':
            protocol().write(QueryMaker.name(self.name()), sck)
        elif query == 'on':
            protocol().write(QueryMaker.on(self.on()), sck)
        elif query == 'overload':
            protocol().write(QueryMaker.overload(self.overload()), sck)
        elif query == 'get':
            if not 'id' in query: return self.logger.err("Wrong request")
            t, v = get_value_query(self[query['id']])
            protocol().write(query.morph(value_type=t, value=v).query(), sck) #morphing query instead of use global_get
        elif query == 'set':
            if not 'id' in query or not 'value_type' in query or not 'value' in query: return self.logger.err("Wrong request")
            self[query['id']] = eval_from_query(query['value_type'], query['value'], (self.envGlobals,{}))
            protocol().write(QueryMaker.ok(), sck)
        elif query == 'call':
            if not 'id' in query or not 'args' in query or not 'kwargs' in query: return self.logger.err("Wrong request")
            answer : Any = self.call(query['id'], *query['args'], **query['kwargs'])
            t, v = get_value_query(answer)
            protocol().write(QueryMaker.call(query['id'], t, v), sck)
        elif query == 'callasync':
            res = self.callasync(query['id'], *query['args'], **query['kwargs'])
            protocol().write(QueryMaker.callasync(query['id'], res.resource), sck)
        elif query == 'resource':
            val = self.resource(query['id'], query['delete'])
            t, v = get_value_query(val)
            protocol().write(QueryMaker.resource(query['id'], t, v), sck)
    
    @staticmethod
    def get(addr : str, port : int, timeout : float = 1) -> Union[RemoteBox, None]:
        res = netMap([(addr, port)], timeout)
        return RemoteBox(res[0]) if res else None
    
    @staticmethod
    def seek(addr : Union[str, Tuple[str]], port : Union[int, Tuple[int]], matchs_per_second : int = 1000) -> BoxGroup:
        group = BoxGroup()
        addrs = addr
        if isinstance(addr, str):
            addrs = [addr]
        if isinstance(port, tuple) and len(addrs) != len(port):
            raise Exception('Expecting same number of addresses and ports')
        for sck in netMap(list(zip(addrs, port) if isinstance(port, tuple) else map(lambda a: (a, port), addrs)), 1/matchs_per_second):
            group.add(RemoteBox(sck))
        return group if group else None #avoid return empty group to prevent never ended tasks

    @staticmethod
    def network(port : int, filter : Callable[[str],bool] = None, matchs_per_second : int = 1000) -> BoxGroup:
        #only valid for IPV4
        thisip = ip()[-1]
        modableip = '.'.join(thisip.split('.')[0:-1]) + "."
        group = Box.seek([modableip + str(num) for num in range(0, 256)], port, matchs_per_second)
        if filter and group:
            group.filter(filter)
        return group

class RemoteBox(IBox):
    def __init__(self, client : ISocket) -> None:
        super().__init__()
        self.client = client
        self.remote_name = None
        self.logger = Logger("Remote::"+self.name())

    def __del__(self):
        self.client.close()

    def name(self) -> str:
        if self.remote_name == None:
            self.remote_name = QueryReader(protocol().ask(QueryMaker.name_req(), self.client)).value()
        return self.remote_name

    def on(self) -> bool:
        return QueryReader(protocol().ask(QueryMaker.on_req(), self.client)).value()

    def overload(self) -> int:
        return QueryReader(protocol().ask(QueryMaker.overload_req(), self.client)).value()

    def call(self, name: str, *args, **kwargs) -> Any:
        query = QueryReader(protocol().ask(QueryMaker.call_req(name, *args, **kwargs), self.client))
        return eval_from_query(query['value_type'], query['value'], ({}, {}))

    def callasync(self, name: str, *args, **kwargs) -> Resource:
        query = QueryReader(protocol().ask(QueryMaker.callasync_req(name, *args, **kwargs), self.client))
        return Resource(query['value'], self)

    def resource(self, id : int, delete : bool) -> Any:
        ans = QueryReader(protocol().ask(QueryMaker.resource_req(id, delete), self.client))
        return eval_from_query(ans['value_type'], ans['value'], ({}, {}))

    def __setitem__(self, k: str, v: Any) -> None:
        t, v = get_value_query(v)
        protocol().ask(QueryMaker.set_req(k, t, v), self.client)

    def __getitem__(self, k: str) -> str:
        query = QueryReader(protocol().ask(QueryMaker.get_req(k), self.client))
        if not 'value_type' in query or not 'value' in query: return self.logger.err("Wrong answer")
        return eval_from_query(query['value_type'], query['value'], ({}, {}))

    def group(self) -> BoxGroup:
        return BoxGroup({self})

class BoxGroup(Set[IBox]):
    def __eq__(self, o: object) -> bool:
        if o == None and len(self) == 0: return True #empty group is equals to void group
        return super().__eq__(o)
    
    def filter(self, fn : Callable[[str],bool]) -> None:
        ln = set()
        for x in self:
            if not fn(x.name()):
                ln.add(x)
        self -= ln

    def __str__(self) -> str:
        return "BoxGroup{%s}" % ', '.join([box.name() for box in self])

    def members(self) -> dict[str, Box]:
        result : dict[str, Box] = {}
        for x in self:
            result[x.name()] = x
        return result

    def set(self, **kwargs):
        for k, v in kwargs.items():
            for x in self:
                x[k] = v

    def call(self, name: str, *args, **kwargs) -> Union[Any, List[Any]]:
        res = []
        for x in self:
            res.append(x.call(name, *args, **kwargs))
        return res[0] if len(res) == 1 else res

    def callasync(self, name: str, *args, **kwargs) -> Union[Resource, List[Resource]]:
        res = []
        for x in self:
            res.append(x.callasync(name, *args, **kwargs))
        return res[0] if len(res) == 1 else res

    def spread(self, function : Union[FunctionWrapper, List[FunctionWrapper]], mode : int = 2) -> Union[Any, List[Any], None]: #mode may be 0(subscription), 1(call), 2(both)
        mode %= 3
        fns : List[FunctionWrapper] = function
        if isinstance(function, FunctionWrapper):
            fns = [function]
        boxes : List[IBox] = [box for box in list(self) if box.on()]
        if len(boxes) == 0:
            raise Exception('No boxes available')
        boxes = sorted(boxes, key=lambda e : e.overload())
        ret : List[Any] = []
        for i in range(0, len(fns)):
            fn : FunctionWrapper = fns[i]
            if mode != 1:
                boxes[i % len(boxes)][fn.name] = fn
            if mode != 0:
                res = boxes[i % len(boxes)].call(fn.name, *fn.args(), **fn.kwargs())
                ret.append(res)
        if mode != 0:
            if isinstance(function, FunctionWrapper):
                    return ret[0]
            return ret