from collections import defaultdict
import threading
import datetime
import tabulate
import copy
import time
import uuid
import sys
import os

from waldorf.threading_u import DSemaphore
from waldorf.cfg import WaldorfCfg
from waldorf.util import *
import waldorf


class WaldorfAPI(object):
    """Event names used by Waldorf"""
    ECHO = 'echo'
    GET_ENV = 'get_env'
    SET_LIMIT = 'set_limit'
    REG_TASK = 'reg_task'
    FREEZE = 'freeze'
    SUBMIT = 'submit'
    CLEAN_UP = 'clean_up'
    GEN_GIT_C = 'gen_git_c'
    CHECK_GIT_C = 'check_git_c'
    GET_INFO = 'get_info'
    CHANGE_CORE = 'change_core'
    RESTART_TASK = 'restart_task'
    ASSIGN_CORE = 'assign_core'
    SYNC = 'sync'
    EXIT = 'exit'

    # Admin
    UP_TIME = 'up_time'
    UPDATE_TABLE = 'update_table'


class TableHeader(object):
    HOSTNAME = 'Hostname'
    TYPE = 'Type'
    STATE = 'State'
    CONN_TIME = 'ConnTime'
    DISCONN_TIME = 'DisconnTime'
    UID = 'UID'
    VERSION = 'Version'

    IP = 'IP'
    CPU = 'CPU'
    MEMORY = 'Memory'
    OS = 'OS'

    READY = 'Ready'
    CORES = 'CORES'
    USED = 'USED'
    LOAD_PER = 'LOAD(%)'
    TOTAL_PER = 'TOTAL(%)'
    LOAD_1 = 'LOAD(1)'
    LOAD_5 = 'LOAD(5)'
    LOAD_15 = 'LOAD(15)'


class MachineType(object):
    NONE = 'None'
    MASTER = 'Master'
    SLAVE = 'Slave'
    CLIENT = 'Client'


class MachineState(object):
    INIT = 'Init'
    ONLINE = 'Online'
    OFFLINE = 'Offline'
    OFFLINE_AB = 'Offline(Abnormally)'


class WaldorfPublInfo(object):
    def __init__(self):
        self.type = MachineType.NONE
        self.state = MachineState.ONLINE
        self.uid = str(uuid.uuid4())
        self.sid = ''
        self.version = waldorf.__version__
        self.system_info = get_system_info()
        self.network_info = get_network_info()
        self.conn_time = 0
        self.conn_time_readable = ''
        self.disconn_time = 0
        self.disconn_time_readable = ''
        self.cfg = WaldorfCfg()

    def record_conn_time(self):
        self.conn_time = time.time()
        self.conn_time_readable = \
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def record_disconn_time(self):
        self.disconn_time = time.time()
        self.disconn_time_readable = \
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def to_table_dict(self):
        return {
            TableHeader.HOSTNAME: self.hostname,
            TableHeader.TYPE: self.type,
            TableHeader.STATE: self.state,
            TableHeader.CONN_TIME: self.conn_time_readable,
            TableHeader.DISCONN_TIME: self.disconn_time_readable,
            TableHeader.UID: self.uid,
            TableHeader.VERSION: self.version,
            TableHeader.IP: self.network_info.ip,
            TableHeader.CPU: self.system_info.cpu_type,
            TableHeader.MEMORY: self.system_info.mem,
            TableHeader.OS: self.system_info.os,
        }

    @property
    def hostname(self):
        return self.network_info.hostname


class MasterPublInfo(WaldorfPublInfo):
    def __init__(self):
        super(MasterPublInfo, self).__init__()
        self.type = MachineType.MASTER

        self.up_time = 0
        self.up_time_readable = ''
        self.env_path = sys.executable[:-11]
        self.waldorf_path = self.env_path + \
                            '/lib/python3.6/site-packages/waldorf'

        self.client_num = 0
        self.slave_num = 0
        self.cores = 0

    def record_up_time(self):
        self.up_time = time.time()
        self.up_time_readable = \
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def to_table_dict(self):
        return {
            TableHeader.HOSTNAME: self.hostname,
            TableHeader.TYPE: self.type,
            TableHeader.VERSION: self.version,
            'UpTime': self.up_time_readable,
            TableHeader.IP: self.network_info.ip,
            'Client\nNum': self.client_num,
            'Slave\nNum': self.slave_num,
            'Cores': self.cores,
        }

    def to_table(self):
        table_dict = self.to_table_dict()
        fixed_width = 10
        _keys = list(table_dict.keys())
        _values = []
        for v in table_dict.values():
            _v = str(v)
            _v = '\n'.join(
                [_v[i:i + fixed_width]
                 for i in range(0, len(_v), fixed_width)])
            _values.append(_v)
        return tabulate.tabulate([_values], _keys)


class ClientPublInfo(WaldorfPublInfo):
    def __init__(self):
        super(ClientPublInfo, self).__init__()
        self.type = MachineType.CLIENT


class SlavePublInfo(WaldorfPublInfo):
    def __init__(self):
        super(SlavePublInfo, self).__init__()
        self.type = MachineType.SLAVE
        self.used_cores = 0
        self.load = {
            'per': 0.0, 'per_5': 0.0,
            'total': 0.0, 'total_5': 0.0,
            'avg_1': 0.0, 'avg_5': 0.0, 'avg_15': 0.0
        }
        self.load_str = {
            'per': '0.0%', 'per_5': '0.0%',
            'total': '0.0%', 'total_5': '0.0%',
            'avg_1': '0.0', 'avg_5': '0.0', 'avg_15': '0.0'
        }
        self.ready = ''
        self.cores = self.system_info.cpu_count
        self.cores_uid = [str(uuid.uuid4())
                          for _ in range(self.cores)]

    def to_table_dict(self):
        table_dict = {
            TableHeader.READY: self.ready,
            TableHeader.CORES: self.system_info.cpu_count,
            TableHeader.USED: self.used_cores,
            TableHeader.LOAD_PER: self.load_str['per_5'],
            TableHeader.TOTAL_PER: self.load_str['total_5'],
            TableHeader.LOAD_1: self.load_str['avg_1'],
            TableHeader.LOAD_5: self.load_str['avg_5'],
            TableHeader.LOAD_15: self.load_str['avg_15'],
        }
        table_dict.update(super(SlavePublInfo, self).to_table_dict())
        return table_dict

    def get_used_cores_uid(self):
        if self.used_cores == 0:
            return []
        return copy.deepcopy(self.cores_uid[-self.used_cores:])

    def update_load(self):
        load_avg = list(os.getloadavg())
        self.load['avg_1'] = load_avg[0]
        self.load['avg_5'] = load_avg[1]
        self.load['avg_15'] = load_avg[2]
        self.update_load_str()

    def update_load_str(self):
        for k, v in self.load.items():
            if 'avg' in k:
                self.load_str[k] = '{:.1f}'.format(v)
            else:
                self.load_str[k] = '{:.1f}%'.format(v)


class WaldorfPvteInfo(object):
    def __init__(self):
        self.sync_event = defaultdict(threading.Event)
        self.cfg = WaldorfCfg()

    def set_sync(self, api):
        key = WaldorfAPI.SYNC + '_' + api
        self.sync_event[key].set()

    def wait_sync(self, api):
        key = WaldorfAPI.SYNC + '_' + api
        self.sync_event[key].wait()
        self.sync_event.pop(key)


class MasterPvteInfo(WaldorfPvteInfo):
    def __init__(self):
        super(MasterPvteInfo, self).__init__()
        self.adm_ns = None
        self.clt_ns = None
        self.slv_ns = None

        self.slv_tbl = None
        self.clt_tbl = None

        self.ci = MasterCoresInfo()
        self._tmp_ri = defaultdict(MasterRegisterInfo)
        self.ri = defaultdict(MasterRegisterInfo)

    def update_reg_info(self, uid, api, resp):
        ri = self._tmp_ri[uid]
        if api == 'hostname':
            ri.hostname = resp
        if api == WaldorfAPI.GET_ENV:
            ri.get_env_resp = resp
        if api == WaldorfAPI.REG_TASK:
            ri.reg_task_resp.append(resp)
        if api == WaldorfAPI.FREEZE:
            ri.freeze_resp = resp

    def confirm_reg_info(self, uid):
        self.ri[uid] = self._tmp_ri[uid]

    def get_reg_info(self, uid):
        return self.ri[uid]

    def remove_reg_info(self, uid):
        self._tmp_ri.pop(uid, None)
        self.ri.pop(uid, None)


class SlavePvteInfo(WaldorfPvteInfo):
    def __init__(self):
        super(SlavePvteInfo, self).__init__()
        self.ri = defaultdict(SlaveRegisterInfo)
        self.ci = defaultdict(SlaveCoreInfo)
        self.events = {}
        self.responses = {}
        self.clients = []
        self.sio = None
        self.ns = None
        self.busy = 0
        self.pending_tasks = []


class ClientPvteInfo(WaldorfPvteInfo):
    def __init__(self):
        super(ClientPvteInfo, self).__init__()
        self.ri = ClientRegisterInfo()
        self.ti = defaultdict(TaskInfo)
        self.events = {}
        self.responses = {}
        self.sio = None
        self.ns = None
        self.retry_flag = False
        self.retry_tasks = []
        self.task_num = 1
        self.limit = 1
        self.sema = DSemaphore(self.limit)

    def set_limit(self, limit):
        self.limit = limit
        self.sema.set_value(int(limit * (
                self.cfg.prefetch_multi + self.cfg.limit_bias)))


class MasterRegisterInfo(object):
    def __init__(self):
        self.hostname = ''
        self.prefetch_multi = 1
        self.get_env_resp = None
        self.reg_task_resp = []
        self.freeze_resp = None


class SlaveRegisterInfo(object):
    def __init__(self):
        self.sid = ''
        self.hostname = ''
        self.prefetch_multi = 1
        self.env_args = None
        self.env = None
        self.tasks = {}
        self.worker_args = None


class TaskInfo(object):
    def __init__(self):
        self.info = None
        self.retry_times = 0
        self.submit_time = time.time()


class ClientRegisterInfo(object):
    def __init__(self):
        self.env_args = None
        self.tasks = {}
        self.task_handlers = {}
        self.app_name = ''
        self.app = None


class GroupInfo(object):
    def __init__(self):
        self.uid_info = {}
        self.sid_uid = {}
        self.events = {}
        self.responses = {}


class MasterCoresInfo(object):
    def __init__(self):
        self.cores = []
        self.idle_cores = []
        self.slv_cores = defaultdict(list)
        self.core_slv = {}
        self.core_clt = {}
        self.slv_core_clt = defaultdict(dict)
        self.clt_cores = defaultdict(list)
        self.clt_list = []
        self.clt_limit = {}
        self.each = {}
        self.fulfill = False
        # Indicate if last update core changed any thing
        self.changed = False

    def detach_core(self, core_uid):
        if core_uid in self.core_clt:
            client_uid = self.core_clt.pop(core_uid)
            self.clt_cores[client_uid].remove(core_uid)
        slave_uid = self.core_slv[core_uid]
        self.slv_core_clt[slave_uid][core_uid] = ''

    def attach_core(self, core_uid, client_uid):
        self.core_clt[core_uid] = client_uid
        self.clt_cores[client_uid].append(core_uid)
        slave_uid = self.core_slv[core_uid]
        self.slv_core_clt[slave_uid][core_uid] = client_uid

    def set_clt_limit(self, clt_uid, limit):
        self.clt_limit[clt_uid] = limit
        self.update_cores()

    @staticmethod
    def _cal_each_cores(each: dict, core_num: int, clt_list: list):
        average = core_num // len(clt_list)
        remainder = core_num % len(clt_list)
        for clt_uid in clt_list:
            _r = 0
            if remainder > 0:
                _r += 1
                remainder -= 1
            each[clt_uid] = average + _r

    def cal_each_cores(self):
        clt_list = copy.deepcopy(self.clt_list)
        cores = copy.deepcopy(self.cores)
        pre_each = {}
        self._cal_each_cores(pre_each, len(cores), clt_list)

        each = {}
        for clt_uid, limit in self.clt_limit.items():
            if limit < pre_each[clt_uid]:
                each[clt_uid] = limit
                clt_list.remove(clt_uid)

        if clt_list:
            remain = len(cores) - sum(each.values())
            self._cal_each_cores(each, remain, clt_list)
            self.fulfill = True
        else:
            self.fulfill = False
        self.each = each

    def init_slave_core_client(self, slv_uid):
        for core_uid in self.slv_cores[slv_uid]:
            slave_uid = self.core_slv[core_uid]
            self.slv_core_clt[slave_uid][core_uid] = ''

    @staticmethod
    def check_change(old, new):
        if ','.join(sorted(old.keys())) != ','.join(sorted(new.keys())):
            return True
        for k in old:
            if ','.join(sorted(old[k])) != ','.join(sorted(new[k])):
                return True
        return False

    def update_cores(self, add: list = None, sub: list = None):
        old = copy.deepcopy(self.clt_cores)
        self._update_cores(add, sub)
        new = copy.deepcopy(self.clt_cores)
        self.changed = self.check_change(old, new)

    def _update_cores(self, add: list = None, sub: list = None):
        add = [] if add is None else add
        sub = [] if sub is None else sub

        for core_uid in sub:
            self.detach_core(core_uid)
            if core_uid in self.idle_cores:
                self.idle_cores.remove(core_uid)
            self.cores.remove(core_uid)

        self.cores.extend(add)
        self.idle_cores.extend(add)

        if len(self.clt_list) == 0:
            for core_uid in list(self.core_clt.keys()):
                self.detach_core(core_uid)
                self.idle_cores.append(core_uid)
            assert len(self.idle_cores) == len(set(self.idle_cores))
            return

        self.cal_each_cores()
        more, less = [], []
        for clt_uid in self.clt_list:
            if len(self.clt_cores[clt_uid]) == \
                    self.each[clt_uid]:
                continue
            diff = len(self.clt_cores[clt_uid]) \
                   - self.each[clt_uid]
            if diff > 0:
                more.append((clt_uid, diff))
            else:
                less.append((clt_uid, -diff))

        for clt_uid, diff in more:
            _cores = copy.deepcopy(self.clt_cores[clt_uid])
            for core_uid in _cores[-diff:]:
                self.detach_core(core_uid)
                self.idle_cores.append(core_uid)
            self.clt_cores[clt_uid] = _cores[:-diff]

        assert not self.fulfill or \
               len(self.idle_cores) == sum(l[1] for l in less)
        for clt_uid, diff in less:
            _cores = copy.deepcopy(self.idle_cores)
            for core_uid in _cores[:diff]:
                self.attach_core(core_uid, clt_uid)
            self.idle_cores = _cores[diff:]
        assert not self.fulfill or len(self.idle_cores) == 0

    def add_client(self, clt_info):
        self.clt_list.append(clt_info.uid)
        self.update_cores()

    def remove_client(self, clt_info):
        if clt_info.uid not in self.clt_list:
            return
        self.clt_list.remove(clt_info.uid)
        self.clt_limit.pop(clt_info.uid, None)
        cores = copy.deepcopy(self.clt_cores[clt_info.uid])
        for core_uid in cores:
            self.detach_core(core_uid)
            self.idle_cores.append(core_uid)
        self.update_cores()
        self.clt_cores.pop(clt_info.uid, None)

    def add_slave(self, slv_info):
        assert isinstance(slv_info, SlavePublInfo)
        cores = slv_info.get_used_cores_uid()
        for core_uid in slv_info.cores_uid:
            self.slv_cores[slv_info.uid].append(core_uid)
            self.core_slv[core_uid] = slv_info.uid
        self.init_slave_core_client(slv_info.uid)
        self.update_cores(add=cores)

    def remove_slave(self, slv_info):
        assert isinstance(slv_info, SlavePublInfo)
        cores = slv_info.get_used_cores_uid()
        self.update_cores(sub=cores)
        for core_uid in slv_info.cores_uid:
            self.core_slv.pop(core_uid)
        self.slv_cores.pop(slv_info.uid)


class SlaveCoreInfo(object):
    def __init__(self):
        self.clt_uid = ''
        self.app_name = ''
        self.worker_name = ''
        self.affinity = 0
        self.worker = None
        self.terminator = threading.Event()


class Response(object):
    def __init__(self, hostname, code, resp, clt_sid=''):
        self.hostname = hostname
        self.code = code
        self.resp = resp
        self.clt_sid = clt_sid

    def encode(self):
        return obj_encode(self)

    @staticmethod
    def decode(data):
        obj = obj_decode(data)
        assert isinstance(obj, Response)
        return obj

    def __repr__(self):
        return '{}, {}, {}'.format(self.hostname, self.code, self.resp)


__all__ = ['WaldorfAPI', 'TableHeader', 'MachineState',
           'MasterPublInfo', 'SlavePublInfo', 'ClientPublInfo',
           'MasterPvteInfo', 'SlavePvteInfo', 'ClientPvteInfo',
           'MasterCoresInfo', 'SlaveCoreInfo',
           'MasterRegisterInfo', 'SlaveRegisterInfo',
           'GroupInfo', 'Response', 'TaskInfo']
