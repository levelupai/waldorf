from socketIO_client import SocketIO, SocketIONamespace
import uuid
import multiprocessing as mp
import socket
from celery import Celery
import waldorf
from waldorf import _WaldorfAPI
from waldorf.cfg import WaldorfCfg
from waldorf.env import WaldorfEnv
import sys
from waldorf.util import DummyLogger, init_logger, get_path, \
    ColoredFormatter, get_system_info, obj_encode, get_local_ip
import logging
import pickle
import base64
import threading
import argparse
import psutil
import os
import queue
import time
import copy
import traceback


class CeleryWorker(mp.Process):
    """Create Celery worker in runtime."""

    def __init__(self, env_path: str, app_name: str, tasks: list,
                 multiplier: int, cfg: WaldorfCfg):
        """Initialize worker.

        Create worker using a virtual environment that is
        different from Waldorf slave environment.

        Args:
            env_path: Virtual environment path.
            app_name: Application name.
            tasks: Registered tasks.
            multiplier: Celery setting, w_prefetch_multi.
            cfg: Waldorf configuration.
        """
        super(CeleryWorker, self).__init__()
        self.daemon = True
        self.env_path = env_path
        self.app_name = app_name
        self.tasks = tasks
        self.multiplier = multiplier
        self.cfg = cfg

    def setup_logger(self):
        import logging.handlers
        import sys
        logger = logging.getLogger(self.app_name)
        logger.setLevel(logging.DEBUG)
        logger.propagate = 0
        _cf = ['$GREEN[%(asctime)s]$RESET',
               '[%(name)s]',
               '$BLUE[%(filename)20s:%(funcName)15s:%(lineno)5d]$RESET',
               '[%(levelname)s]',
               ' $CYAN%(message)s$RESET']
        cformatter = ColoredFormatter('-'.join(_cf))
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(cformatter)
        logger.addHandler(ch)
        return logger

    def setup_tasks(self):
        """Set up all tasks."""
        for task_name, task_code, opts in self.tasks:
            # Execute the task code and get the function object.
            exec(task_code, globals(), locals())
            _code = locals()[task_name]
            self.logger.debug('add task {}, {}'.format(task_name, _code))
            self.app.task(**opts)(_code)

    def run(self):
        global __name__
        __name__ = self.app_name
        # Activate virtualenv.
        activate_this = self.env_path + '/bin/activate_this.py'
        exec(open(activate_this).read(), dict(__file__=activate_this))
        if self.cfg.debug >= 1:
            self.logger = self.setup_logger()
        else:
            self.logger = DummyLogger()
        self.cfg.update()
        # Set up Celery worker.
        self.app = Celery(self.app_name,
                          broker=self.cfg.celery_broker,
                          backend=self.cfg.celery_backend)
        self.app.conf.task_default_queue = self.app_name
        self.app.conf.accept_content = ['json', 'pickle']
        self.app.conf.task_serializer = 'pickle'
        self.app.conf.result_serializer = 'pickle'
        self.app.conf.task_acks_late = True
        self.app.conf.worker_lost_wait = 60.0
        self.app.conf.result_expires = 1800
        self.app.conf.w_prefetch_multi = self.multiplier
        self.logger.debug('finish app configure')
        self.setup_tasks()
        if self.cfg.debug >= 1:
            self.app.worker_main(['worker', '-n',
                                  self.app_name.replace('-', '_') + '@%h',
                                  '-c', str(self.cfg.core), '-E'])
        else:
            self.app.worker_main(['worker', '-n',
                                  self.app_name.replace('-', '_') + '@%h',
                                  '-c', str(self.cfg.core)])
        self.logger.debug('end')


class SockWaitThread(threading.Thread):
    """Handle sock wait in an individual thread"""

    def __init__(self, up):
        super(SockWaitThread, self).__init__()
        self.up = up
        self.daemon = True

    def run(self):
        while True:
            # https://github.com/invisibleroads/socketIO-client/issues/148
            try:
                self.up.sock.wait()
            except IndexError:
                # Restart SocketIO connection.
                self.up.logger.debug('Index error. Connect to {}:{} with uid {}'
                                     .format(self.up.cfg.master_ip,
                                             self.up.cfg.waldorf_port,
                                             self.up.uid))
                self.up.sock = SocketIO(self.up.cfg.master_ip,
                                        self.up.cfg.waldorf_port,
                                        cookies=self.up.cookies)
                self.up.slave_ns = self.up.sock.define(Namespace, '/slave')
                self.up.slave_ns.setup(self.up)


class _WaldorfSio(mp.Process):
    def __init__(self, cmd_queue, cfg: WaldorfCfg):
        super(_WaldorfSio, self).__init__()
        self.cmd_queue = cmd_queue
        self.cfg = cfg

    def setup(self):
        # Make a random UUID for this session (re-used on reconnect).
        self.uid = str(uuid.uuid4())
        self.setup_logger()
        self.system_info = get_system_info()
        self.waldorf_info = {'uid': self.uid,
                             'hostname': socket.gethostname(),
                             'ver': waldorf.__version__,
                             'ip': get_local_ip(),
                             'os': self.system_info.os,
                             'cpu_type': self.system_info.cpu_type,
                             'cpu_count': self.system_info.cpu_count,
                             'cfg_core': self.cfg.core,
                             'mem': self.system_info.mem}
        self.sock = SocketIO(self.cfg.master_ip, self.cfg.waldorf_port)
        self.logger.debug('Connect to {}:{} with uid {}'.format(
            self.cfg.master_ip, self.cfg.waldorf_port, self.uid))
        self.slave_ns = self.sock.define(Namespace, '/slave')
        self.slave_ns.setup(self)
        self.events = {}
        self.info = {}

    def setup_logger(self):
        if self.cfg.debug >= 1:
            self.logger = init_logger(
                'wd_slave', get_path(relative_path='.'),
                (logging.DEBUG, logging.DEBUG))
        else:
            self.logger = DummyLogger()

    def put(self, r):
        self.cmd_queue[1].put(r)

    def check_ver(self):
        # TODO: Move this part to slave namespace?
        self.logger.debug('enter on_check_ver')
        self.slave_ns.emit(_WaldorfAPI.CHECK_VER, waldorf.__version__)
        self.events['check_ver'] = threading.Event()
        self.events['check_ver'].wait()
        self.put(self.info['check_ver_resp'])
        self.logger.debug('leave on_check_ver')

    def run(self):
        self.setup()
        SockWaitThread(self).start()
        self.check_ver()
        while True:
            cmd = self.cmd_queue[0].get()
            if cmd[0] == 'exit':
                # TODO: Move this part to slave namespace?
                self.slave_ns.emit(_WaldorfAPI.EXIT, self.uid)
                for uid in self.slave_ns.workers:
                    print('Stop worker, uid: {}'.format(
                        uid, self.slave_ns.workers[uid][3]))
                    for task in self.slave_ns.workers[uid][3]:
                        print('task name: {}'.format(task[0]))
                    self.slave_ns.info[uid]['worker'].terminate()
                break
        self.sock.disconnect()
        self.cmd_queue[1].put(0)
        self.logger.debug('end')


class CheckCPUThread(threading.Thread):
    """Monitor CPU usage and change w_prefetch_multi argument dynamically."""

    def __init__(self, q):
        super(CheckCPUThread, self).__init__()
        self.q = q
        self.daemon = True

    def run(self):
        while True:
            core = self.q[0].get()
            affinity = [i for i in range(mp.cpu_count())][-core:]
            # Calculate prefetch argument.
            average = 0
            for _ in range(5):
                per = psutil.cpu_percent(interval=1, percpu=True)
                aver = sum([per[i] for i in affinity]) / core
                average += aver
            average /= 5
            w_prefetch_multi = max(0, 50 - average) // 20 + 1
            self.q[1].put(w_prefetch_multi)


class Namespace(SocketIONamespace):
    """Slave namespace."""

    def setup(self, up: _WaldorfSio):
        self.up = up
        self.info = {}
        self._code = None
        self.affinity = [i for i in range(mp.cpu_count())][-self.up.cfg.core:]
        self.workers = {}
        self.check_q = [queue.Queue(), queue.Queue()]
        self.check_thread = CheckCPUThread(self.check_q)
        self.check_thread.start()
        self.w_prefetch_multi = 4
        self.busy = False
        self.emit(_WaldorfAPI.GET_INFO + '_resp',
                  obj_encode(self.up.waldorf_info))
        threading.Thread(target=self.update, daemon=True).start()

    def update(self):
        time.sleep(10)
        if len(self.workers.keys()) == 0 and not self.busy:
            self.check_q[0].put(self.up.cfg.core)
        if self.check_q[1].qsize() != 0:
            w_prefetch_multi = int(self.check_q[1].get())
            if w_prefetch_multi != self.w_prefetch_multi and not self.busy and \
                    len(self.workers.keys()) == 0:
                if len(self.workers.keys()) == 0:
                    print('w_prefetch_multi argument: {} -> {}'.
                          format(self.w_prefetch_multi,
                                 w_prefetch_multi))
                    self.w_prefetch_multi = w_prefetch_multi
                else:
                    print('w_prefetch_multi argument is discarded: {}'.
                          format(w_prefetch_multi))
        threading.Thread(target=self.update).start()

    def log(self, msg):
        if hasattr(self, 'up'):
            self.up.logger.debug(msg)

    def on_connect(self):
        print('on_connect')

    def on_reconnect(self):
        self.log('on_reconnect')
        self.emit(_WaldorfAPI.GET_INFO + '_resp',
                  obj_encode(self.up.waldorf_info))

    def get_info_dict(self, uid):
        if uid not in self.info:
            self.info[uid] = {}
            self.info[uid]['tasks'] = []
        return self.info[uid]

    def on_echo(self, sid):
        self.emit(_WaldorfAPI.ECHO + '_resp', sid)

    def get_env(self, uid, args):
        """Set up virtual environment."""
        self.busy = True
        info = self.get_info_dict(uid)
        name, pairs, suites, cfg = pickle.loads(base64.b64decode(args))
        info['get_env'] = [name, pairs, suites, cfg]
        self.env = WaldorfEnv(name, cfg)
        resp = self.env.get_env(pairs, suites)
        hostname = socket.gethostname()
        self.emit(_WaldorfAPI.GET_ENV + '_resp', (uid, hostname, resp))
        self.busy = False

    def on_get_env(self, uid, args):
        self.log('on_get_env')
        threading.Thread(target=self.get_env,
                         args=(uid, args), daemon=True).start()

    def on_reg_task(self, uid, task_name, task_code, opts):
        self.log('on_reg_task')
        info = self.get_info_dict(uid)
        info['tasks'].append([task_name, task_code, opts])

    def setup_worker(self, args):
        """Set up Celery worker."""
        uid, py_path, env_path, tasks, prefetch_multiplier, cfg = args
        app_name = 'app-' + uid
        mp.set_executable(py_path)
        w = CeleryWorker(env_path, app_name,
                         tasks, prefetch_multiplier, cfg)
        w.start()
        # Fix which cores will be used.
        os.system('taskset -pc {} {}'.format(
            ','.join([str(i) for i in self.affinity]), w.pid))
        self.info[uid]['worker'] = w
        mp.set_executable(sys.executable)

    def on_freeze(self, uid, sid):
        """Freeze worker configuration and set up worker."""
        self.busy = True
        self.log('on_freeze')
        args = [uid, self.env.get_py_path(), self.env.get_env_path(),
                self.get_info_dict(uid)['tasks']]
        self.workers[uid] = args
        args = copy.deepcopy(args)
        args.extend([self.w_prefetch_multi, self.up.cfg])
        self.setup_worker(args)
        self.emit(_WaldorfAPI.FREEZE + '_resp', sid)
        self.busy = False

    def on_check_ver_resp(self, version):
        self.log('on_check_ver_resp')
        self.up.info['check_ver_resp'] = version
        self.up.events['check_ver'].set()

    def on_ver_mismatch(self, version):
        print('Warning: Version mismatch. Local version: {}. '
              'Master version: {}. Please reconfigure waldorf!'
              .format(waldorf.__version__, version))

    def on_clean_up(self, uid):
        """Clean up and terminate Celery worker."""
        self.log('on_clean_up')
        if uid in self.info and 'worker' in self.info[uid]:
            self.info[uid]['worker'].terminate()
            self.workers.pop(uid, None)

    def on_change_core(self, core):
        """Change core usage on runtime."""
        self.busy = True
        self.log('on_change_core')
        try:
            # Set up affinity.
            self.up.cfg.core = core
            self.up.waldorf_info['cfg_core'] = core
            self.affinity = [i for i in range(mp.cpu_count())][
                            -self.up.cfg.core:]

            # Stop workers.
            for uid in self.workers:
                self.info[uid]['worker'].terminate()

            self.log('terminated and wait for 1 seconds')
            time.sleep(1)

            # Restart workers one by one.
            for uid in self.workers:
                args = self.workers[uid]
                args = copy.deepcopy(args)
                args.extend([self.w_prefetch_multi, self.up.cfg])
                self.setup_worker(args)
            self.emit(_WaldorfAPI.CHANGE_CORE + '_resp', (0, 'Success'))
            self.log('Success')
        except Exception as e:
            self.emit(_WaldorfAPI.CHANGE_CORE + '_resp',
                      (-1, traceback.format_exc()))
        self.busy = False


class WaldorfSlave(object):
    def __init__(self, cfg: WaldorfCfg):
        self.cfg = cfg
        self.debug = cfg.debug
        self.setup_logger()
        self._sio_queue = [mp.Queue(), mp.Queue()]
        self._sio_p = _WaldorfSio(self._sio_queue, self.cfg)
        self._sio_p.start()
        version = self._sio_queue[1].get()
        if version != waldorf.__version__:
            raise Exception('Version mismatch. Local version: {}. '
                            'Master version: {}.'
                            .format(waldorf.__version__, version))

    def setup_logger(self):
        if self.debug >= 2:
            _cf = ['$GREEN[%(asctime)s]$RESET',
                   '[%(name)s]',
                   '$BLUE[%(filename)20s:%(funcName)15s:%(lineno)5d]$RESET',
                   '[%(levelname)s]',
                   ' $CYAN%(message)s$RESET']
            cformatter = ColoredFormatter('-'.join(_cf))

            logger = logging.getLogger('socketIO-client')
            logger.setLevel(logging.DEBUG)
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(cformatter)
            logger.addHandler(ch)

    def loop(self):
        try:
            while True:
                cmd = input('cmd:\n')
                if cmd == 'exit':
                    print('exiting')
                    self._sio_queue[0].put((cmd,))
                    self._sio_queue[1].get()
                    break
        except KeyboardInterrupt:
            self._sio_queue[0].put(('exit',))
            self._sio_queue[1].get()
        print('end')


def parse_args():
    cfg = WaldorfCfg()
    parser = argparse.ArgumentParser(description='Waldorf slave')
    parser.add_argument('-i', '--ip', type=str, default=cfg.master_ip)
    parser.add_argument('-p', '--port', type=int, default=cfg.waldorf_port)
    parser.add_argument('-c', '--core', type=int, default=cfg.core)
    parser.add_argument('--broker', type=str, choices=['rabbit', 'redis'],
                        default=cfg.broker)
    parser.add_argument('--backend', type=str, choices=['memcached', 'redis'],
                        default=cfg.backend)
    parser.add_argument('--broker_ip', default=None)
    parser.add_argument('--backend_ip', default=None)
    parser.add_argument('--redis_port', type=int, default=cfg.redis_port)
    parser.add_argument('--memcached_port', type=int,
                        default=cfg.memcached_port)
    parser.add_argument('-d', '--debug', type=int, default=cfg.debug)
    args = parser.parse_args()
    cfg.set_ip(args.ip, args.broker_ip, args.backend_ip)
    cfg.waldorf_port = args.port
    cfg.core = min(args.core, mp.cpu_count())
    cfg.broker = args.broker
    cfg.backend = args.backend
    cfg.redis_port = args.redis_port
    cfg.memcached_port = args.memcached_port
    cfg.debug = args.debug
    cfg.update()
    return cfg


if __name__ == '__main__':
    mp.set_start_method('spawn')
    cfg = parse_args()
    slave = WaldorfSlave(cfg)
    slave.loop()
