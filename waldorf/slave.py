from collections import deque
import multiprocessing as mp
from pathlib import Path
import threading
import traceback
import argparse
import socket
import pickle
import base64
import psutil
import time
import uuid
import copy
import sys
import os

from socketIO_client import SocketIO, SocketIONamespace
from celery.app.control import Control
from celery import Celery
import redis

from waldorf.util import DummyLogger, get_frame, init_logger, get_path, \
    get_timestamp, ColoredFormatter, get_system_info, obj_encode, get_local_ip
from waldorf.cfg import WaldorfCfg, load_cfg, save_cfg
from waldorf.env import WaldorfEnv
from waldorf import _WaldorfAPI
import waldorf


class CeleryWorker(mp.Process):
    """Create Celery worker in runtime."""

    def __init__(self, env_path: str, app_name: str, worker_name: str,
                 tasks: list, multiplier: int, cfg: WaldorfCfg):
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
        self.worker_name = worker_name
        self.tasks = tasks
        self.multiplier = multiplier
        self.cfg = cfg

    def setup_logger(self, level):
        import logging
        return init_logger(self.app_name, get_path(relative_path='.'),
                           (level, logging.INFO), (True, False))

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
            import logging
            self.logger = self.setup_logger(logging.DEBUG)
        else:
            import logging
            self.logger = self.setup_logger(logging.INFO)
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
        self.app.conf.worker_prefetch_multiplier = self.multiplier
        self.logger.debug('prefetch multiplier: {}'.format(self.multiplier))
        self.logger.debug('finish app configure')
        self.setup_tasks()
        if self.cfg.debug >= 2:
            self.app.worker_main(['worker', '-n', self.worker_name,
                                  '-c', str(self.cfg.core), '-E'])
        else:
            self.app.worker_main(['worker', '-n', self.worker_name,
                                  '-c', str(self.cfg.core)])
        self.logger.debug('end')


class SockWaitThread(threading.Thread):
    """Handle sock wait in an individual thread"""

    def __init__(self, up):
        super(SockWaitThread, self).__init__()
        self.up = up
        self.daemon = True

    def run(self):
        while self.up.alive:
            # https://github.com/invisibleroads/socketIO-client/issues/148
            try:
                self.up.sock.wait()
            except IndexError:
                # Restart SocketIO connection.
                self.up.logger_output('debug',
                                      'Index error. Connect to {}:{} with uid {}'
                                      .format(self.up.cfg.master_ip,
                                              self.up.cfg.waldorf_port,
                                              self.up.uid),
                                      get_frame())
                self.up.sock = SocketIO(self.up.cfg.master_ip,
                                        self.up.cfg.waldorf_port)
                self.up.slave_ns = self.up.sock.define(Namespace, '/slave')
                self.up.slave_ns.setup(self.up)


class CheckCPUThread(threading.Thread):
    """Monitor CPU usage and change load average."""

    def __init__(self, up):
        super(CheckCPUThread, self).__init__()
        self.up = up
        self.cpu_count = mp.cpu_count()
        self.deque = deque(maxlen=30)
        self.daemon = True

    def run(self):
        while self.up.alive:
            core = self.up.waldorf_info['cfg_core']
            if core == 0:
                self.up.waldorf_info['load_per'] = '0.0%'
                self.up.waldorf_info['load_total'] = '0.0%'
                time.sleep(0.01)
                continue
            affinity = [i for i in range(self.cpu_count)][-core:]
            # Calculate prefetch argument.
            average = 0
            total = 0
            for _ in range(10):
                per = psutil.cpu_percent(interval=1, percpu=True)
                aver = sum([per[i] for i in affinity]) / core
                average += aver
                total += sum(per) / self.cpu_count
            average /= 10
            total /= 10
            self.deque.append(average)
            self.up.waldorf_info['load_per'] = '{:.1f}%'.format(average)
            self.up.waldorf_info['load_total'] = '{:.1f}%'.format(total)
            self.up.waldorf_info['load_per_5'] = sum(self.deque) / \
                                                 len(self.deque)


class _WaldorfSio(mp.Process):
    def __init__(self, sio_queue, cfg: WaldorfCfg):
        super(_WaldorfSio, self).__init__()
        self.sio_queue = sio_queue
        self.cfg = cfg
        self.alive = True

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
                             'mem': self.system_info.mem,
                             'load_per': '0.0%',
                             'load_total': '0.0%',
                             'load_avg_1': '0.0',
                             'load_avg_5': '0.0',
                             'load_avg_15': '0.0',
                             'prefetch_multi': '1',
                             'ready': ' '}
        self.update_load_avg()
        self.sock = SocketIO(self.cfg.master_ip, self.cfg.waldorf_port)
        self.logger.debug('Connect to {}:{} with uid {}'.format(
            self.cfg.master_ip, self.cfg.waldorf_port, self.uid))
        self.slave_ns = self.sock.define(Namespace, '/slave')
        self.slave_ns.setup(self)
        self.events = {}
        self.info = {}

    def update_load_avg(self):
        load_avg = list(os.getloadavg())
        load_avg = [str(round(i, 1)) for i in load_avg]
        self.waldorf_info['load_avg_1'] = load_avg[0]
        self.waldorf_info['load_avg_5'] = load_avg[1]
        self.waldorf_info['load_avg_15'] = load_avg[2]

    def setup_logger(self):
        if self.cfg.debug >= 1:
            import logging
            self.logger = init_logger(
                'wd_slave', get_path(relative_path='.'),
                (logging.DEBUG, logging.DEBUG))
        else:
            import logging
            self.logger = init_logger(
                'wd_slave', get_path(relative_path='.'),
                (logging.INFO, logging.INFO), (True, False))

        # logger for writing local log
        self.temp = True
        dir = get_path('slave_log', abspath=str(Path.home()) + '/.waldorf')
        file_name = get_timestamp()
        self.temp_logger = init_logger(
            file_name, dir, (logging.DEBUG, logging.DEBUG), (False, True))
        while len(os.listdir(dir)) > 5:
            to_delete = sorted(os.listdir(dir))[0]
            os.remove(os.path.join(dir, to_delete))

    def logger_output(self, level, msg, frame):
        if level == 'info':
            self.logger.info(msg, frame=frame)
            if self.temp:
                self.temp_logger.info(msg, frame=frame)
        if level == 'debug':
            self.logger.debug(msg, frame=frame)
            if self.temp:
                self.temp_logger.debug(msg, frame=frame)

    def put(self, r):
        self.sio_queue[1].put(r)

    def check_ver(self):
        self.logger_output('debug', 'enter on_check_ver', get_frame())
        self.slave_ns.emit(_WaldorfAPI.CHECK_VER, waldorf.__version__)
        self.events['check_ver'] = threading.Event()
        self.events['check_ver'].wait()
        self.put(self.info['check_ver_resp'])
        self.logger_output('debug', 'leave on_check_ver', get_frame())

    def run(self):
        self.setup()
        CheckCPUThread(self).start()
        SockWaitThread(self).start()
        self.check_ver()

        while True:
            cmd = self.sio_queue[0].get()
            if cmd[0] == 'exit':
                self.slave_ns.emit(_WaldorfAPI.EXIT, self.uid)
                for uid in self.slave_ns.workers:
                    print('Stop worker, uid: {}'.format(
                        uid, self.slave_ns.workers[uid][3]))
                    for task in self.slave_ns.workers[uid][3]:
                        print('task name: {}'.format(task[0]))
                    if 'worker' in self.slave_ns.info[uid]:
                        self.slave_ns.terminate_worker(uid)
                self.alive = False
                break
        self.sock.disconnect()
        self.sio_queue[1].put(0)
        self.logger_output('debug', 'end', get_frame())


class TerminatorThread(threading.Thread):
    """Terminate celery worker using app control."""

    def __init__(self, up, app_name, worker_name, cfg, event: threading.Event):
        super(TerminatorThread, self).__init__()
        self.up = up
        self.app_name = app_name
        self.worker_name = worker_name
        self.cfg = cfg
        self.event = event
        self.daemon = True

    def run(self):
        time.sleep(2)
        app = Celery(self.app_name,
                     broker=self.cfg.celery_broker,
                     backend=self.cfg.celery_backend)
        c = Control(app)
        self.up.log(c.ping(destination=[self.worker_name]), get_frame())
        self.event.wait()
        c.shutdown(destination=[self.worker_name])


class Namespace(SocketIONamespace):
    """Slave namespace."""

    def setup(self, up: _WaldorfSio):
        self.up = up
        self.info = {}
        self._code = None
        self.affinity = [i for i in range(mp.cpu_count())][-self.up.cfg.core:]
        self.envs = {}
        self.workers = {}
        self.w_prefetch_multi = 1
        self.busy = 0
        self.worker_lock = threading.Lock()
        self.up.waldorf_info['ready'] = 'True'
        self.emit(_WaldorfAPI.GET_INFO + '_resp',
                  obj_encode(self.up.waldorf_info))
        self.time = time.time()
        self.current_client = []
        threading.Thread(target=self.update, daemon=True).start()

    def update(self):
        time.sleep(10)

        if len(self.workers.keys()) == 0 and self.busy == 0:
            self.time = time.time()
            self.w_prefetch_multi = 1
            self.up.waldorf_info['prefetch_multi'] = self.w_prefetch_multi
        if len(self.workers.keys()) != 0 and time.time() - self.time > 300:
            flag = False
            if self.up.waldorf_info['cfg_core'] * 0.95 <= \
                    self.up.waldorf_info['load_per_5'] and \
                    self.w_prefetch_multi > 1:
                self.up.logger_output('info',
                                      'w_prefetch_multi argument: {} -> {}'
                                      .format(self.w_prefetch_multi,
                                              self.w_prefetch_multi - 1),
                                      get_frame())
                self.w_prefetch_multi -= 1
                flag = True
            if self.up.waldorf_info['cfg_core'] * 0.7 > \
                    self.up.waldorf_info['load_per_5'] and \
                    self.w_prefetch_multi < 6:
                self.up.logger_output('info',
                                      'w_prefetch_multi argument: {} -> {}'
                                      .format(self.w_prefetch_multi,
                                              self.w_prefetch_multi + 1),
                                      get_frame())
                self.w_prefetch_multi += 1
                flag = True
            if flag:
                self.busy += 1
                self.up.waldorf_info['ready'] = 'True' \
                    if self.busy == 0 else 'False'
                self.log('changed prefetch_multi due to load average',
                         get_frame())
                self.time = time.time()
                self.up.waldorf_info['prefetch_multi'] = self.w_prefetch_multi

                if self.up.waldorf_info['cfg_core'] > 0:
                    self.worker_lock.acquire()
                    for uid in self.workers:
                        if 'worker' in self.info[uid]:
                            self.terminate_worker(uid)
                    self.worker_lock.release()

                    self.log('terminated and wait for 1 seconds',
                             get_frame())
                    time.sleep(1)

                    # Restart workers one by one.
                    self.worker_lock.acquire()
                    for uid in self.workers:
                        args = self.workers[uid]
                        args = copy.deepcopy(args)
                        args.extend([self.w_prefetch_multi, self.up.cfg])
                        self.setup_worker(args)
                    self.worker_lock.release()

                self.busy -= 1
                self.up.waldorf_info['ready'] = 'True' \
                    if self.busy == 0 else 'False'

        threading.Thread(target=self.update).start()

    def log(self, msg, frame):
        if hasattr(self, 'up'):
            self.up.logger_output('debug', msg, frame)

    def on_connect(self):
        print('on_connect')

    def on_reconnect(self):
        self.log('on_reconnect', get_frame())
        self.emit(_WaldorfAPI.GET_INFO + '_resp',
                  obj_encode(self.up.waldorf_info))

    def get_info_dict(self, uid):
        if uid not in self.info:
            self.info[uid] = {}
            self.info[uid]['tasks'] = []
        return self.info[uid]

    def on_echo(self, sid):
        self.emit(_WaldorfAPI.ECHO + '_resp', sid)

    def get_env(self, uid, args, restart):
        """Set up virtual environment."""
        self.busy += 1
        self.up.waldorf_info['ready'] = 'True' if self.busy == 0 else 'False'
        info = self.get_info_dict(uid)
        name, pairs, suites, cfg = pickle.loads(base64.b64decode(args))
        info['get_env'] = [name, pairs, suites, cfg]
        self.envs[uid] = WaldorfEnv(name, cfg, self.up.logger)
        resp = self.envs[uid].get_env(pairs, suites)
        hostname = socket.gethostname()
        self.emit(_WaldorfAPI.GET_ENV + '_resp', (uid, hostname, resp, restart))
        self.busy -= 1
        self.up.waldorf_info['ready'] = 'True' if self.busy == 0 else 'False'

    def on_get_env(self, uid, args, restart=False):
        self.log('on_get_env', get_frame())
        threading.Thread(target=self.get_env,
                         args=(uid, args, restart), daemon=True).start()

    def on_reg_task(self, uid, task_name, task_code, opts):
        self.log('on_reg_task', get_frame())
        info = self.get_info_dict(uid)
        info['tasks'].append([task_name, task_code, opts])

    def setup_app_control(self, app_name, worker_name, cfg, event):
        TerminatorThread(self, app_name, worker_name, cfg, event).start()
        time.sleep(2)

    def setup_worker(self, args):
        """Set up Celery worker."""
        uid, py_path, env_path, tasks, prefetch_multiplier, cfg = args
        app_name = 'app-' + uid
        self.info[uid]['app_name'] = app_name
        worker_name = app_name.replace('-', '_') + \
                      '@{}'.format(socket.gethostname())
        self.log('setup worker for uid: {}'.format(uid), get_frame())
        mp.set_executable(py_path)
        w = CeleryWorker(env_path, app_name, worker_name,
                         tasks, prefetch_multiplier, cfg)
        w.start()
        # Fix which cores will be used.
        os.system('taskset -pc {} {}'.format(
            ','.join([str(i) for i in self.affinity]), w.pid))
        self.info[uid]['worker'] = w
        mp.set_executable(sys.executable)

        # Test app control
        event = threading.Event()
        self.info[uid]['event'] = event
        self.setup_app_control(app_name, worker_name, cfg, event)

    def terminate_worker(self, uid):
        try:
            self.info[uid]['event'].set()
            time.sleep(2)
            self.info[uid]['worker'].terminate()
            time.sleep(2)
        except:
            self.log('terminate worker error. exception: {}'.format(
                traceback.format_exc()), get_frame())

    def on_freeze(self, uid, sid, restart=False):
        """Freeze worker configuration and set up worker."""
        while self.busy > 0:
            time.sleep(0.5)
        self.busy += 1
        self.up.waldorf_info['ready'] = 'True' if self.busy == 0 else 'False'
        self.log('on_freeze', get_frame())
        args = [uid, self.envs[uid].get_py_path(),
                self.envs[uid].get_env_path(),
                self.get_info_dict(uid)['tasks']]
        self.worker_lock.acquire()
        self.workers[uid] = args
        self.worker_lock.release()
        args = copy.deepcopy(args)
        args.extend([self.w_prefetch_multi, self.up.cfg])
        if self.up.waldorf_info['cfg_core'] != 0:
            self.setup_worker(args)
        self.current_client.append(uid)
        self.emit(_WaldorfAPI.FREEZE + '_resp', (sid, restart))
        self.busy -= 1
        self.up.waldorf_info['ready'] = 'True' if self.busy == 0 else 'False'

    def on_check_ver_resp(self, version):
        self.log('on_check_ver_resp', get_frame())
        self.up.info['check_ver_resp'] = version
        self.up.events['check_ver'].set()

    def on_ver_mismatch(self, version):
        print('Warning: Version mismatch. Local version: {}. '
              'Master version: {}. Please reconfigure waldorf!'
              .format(waldorf.__version__, version))

    def on_clean_up(self, uid):
        """Clean up and terminate Celery worker."""
        self.log('on_clean_up', get_frame())
        self.log('request client uid: {}'.format(uid), get_frame())

        if uid in self.info:
            if uid in self.current_client:
                self.log('removing client uid: {}'.format(uid), get_frame())
                self.current_client.remove(uid)
            if 'worker' in self.info[uid]:
                self.log('terminate worker of uid {}'.format(uid),
                         get_frame())
                self.terminate_worker(uid)
            self.info.pop(uid, None)
            self.worker_lock.acquire()
            self.workers.pop(uid, None)
            self.worker_lock.release()

        self.log('current running client(s):', get_frame())
        for client_uid in self.current_client:
            self.log(client_uid, get_frame())

    def on_change_core(self, core, cur_clients):
        """Change core usage on runtime."""
        self.busy += 1
        self.up.waldorf_info['ready'] = 'True' if self.busy == 0 else 'False'
        self.log('on_change_core', get_frame())
        try:
            # Update local clients to master's
            uids = list(cur_clients.keys())
            for uid in copy.deepcopy(self.current_client):
                if uid in uids:
                    continue
                self.log('local client not found on master, uid {}'.
                         format(uid), get_frame())
                if uid in self.info and 'worker' in self.info[uid]:
                    self.log('terminate worker of uid {}'.format(uid),
                             get_frame())
                    if uid in self.current_client:
                        self.log('removing client uid: {}'.format(uid),
                                 get_frame())
                        self.current_client.remove(uid)
                    self.worker_lock.acquire()
                    self.terminate_worker(uid)
                    self.workers.pop(uid, None)
                    self.worker_lock.release()

            # Set up affinity.
            self.up.cfg.core = core
            self.up.waldorf_info['cfg_core'] = core
            self.affinity = [i for i in range(mp.cpu_count())][
                            -self.up.cfg.core:]
            self.log('changed core to {}'.format(core), get_frame())

            # Stop workers.
            self.worker_lock.acquire()
            for uid in self.workers:
                self.terminate_worker(uid)
            self.worker_lock.release()

            self.log('terminated and wait for 1 seconds', get_frame())
            time.sleep(1)

            if core != 0:
                # Restart workers one by one.
                self.worker_lock.acquire()
                for uid in self.workers:
                    args = self.workers[uid]
                    args = copy.deepcopy(args)
                    args.extend([self.w_prefetch_multi, self.up.cfg])
                    self.setup_worker(args)
                self.worker_lock.release()
            save_cfg('slave', self.up.cfg)
            self.emit(_WaldorfAPI.CHANGE_CORE + '_resp', (0, 'Success'))
            self.log('Success', get_frame())

        except Exception as e:
            self.log(traceback.format_exc(), get_frame())
            self.emit(_WaldorfAPI.CHANGE_CORE + '_resp',
                      (-1, traceback.format_exc()))
        self.busy -= 1
        self.up.waldorf_info['ready'] = 'True' if self.busy == 0 else 'False'

    def on_update_table(self, args):
        try:
            self.up.update_load_avg()
            new_info = {}
            for arg in args:
                if arg in self.up.waldorf_info:
                    new_info[arg] = self.up.waldorf_info[arg]
            self.emit(_WaldorfAPI.UPDATE_TABLE + '_resp', new_info)
        except Exception as e:
            self.emit(_WaldorfAPI.UPDATE_TABLE + '_resp',
                      traceback.format_exc())

    def on_restart_task(self, args):
        # Update local clients to master's
        uids = list(args.keys())
        for uid in copy.deepcopy(self.current_client):
            if uid in uids:
                continue
            self.log('local client not found on master, uid {}'.format(uid),
                     get_frame())
            if uid in self.info and 'worker' in self.info[uid]:
                self.log('terminate worker of uid {}'.format(uid),
                         get_frame())
                if uid in self.current_client:
                    self.log('removing client uid: {}'.format(uid),
                             get_frame())
                    self.current_client.remove(uid)
                self.worker_lock.acquire()
                self.terminate_worker(uid)
                self.workers.pop(uid, None)
                self.worker_lock.release()

        # Restarting local client tasks
        for uid, arg in args.items():
            if uid in self.current_client:
                continue
            env, tasks, sid = arg
            self.on_get_env(uid, env, restart=True)
            for task in tasks:
                task_name, task_code, opts = task
                self.on_reg_task(uid, task_name, task_code, opts)
                self.log('restarting task {} from client uid {}'.
                         format(task_name, uid), get_frame())
            self.on_freeze(uid, sid, restart=True)


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
            import logging
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
                    print('L634: Exiting')
                    self._sio_queue[0].put((cmd,))
                    self._sio_queue[1].get()
                    break
        except KeyboardInterrupt:
            self._sio_queue[0].put(('exit',))
            self._sio_queue[1].get()
        print('L641: End')


def parse_args():
    cfg = load_cfg('slave')
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
    save_cfg('slave', cfg)
    return cfg


if __name__ == '__main__':
    mp.set_start_method('spawn')
    cfg = parse_args()
    slave = WaldorfSlave(cfg)
    slave.loop()
