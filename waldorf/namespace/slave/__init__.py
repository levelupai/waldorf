import multiprocessing as mp
from pathlib import Path
import threading
import traceback
import time
import uuid
import copy
import sys
import os

from celery.app.control import Control
from socketio import ClientNamespace
from celery import Celery

from waldorf.slave import _WaldorfSio
from waldorf.cfg import WaldorfCfg, save_cfg
from waldorf.env import WaldorfEnv
from waldorf.common import *
from waldorf.util import *

__all__ = ['Namespace']


class CeleryWorker(mp.Process):
    """Create Celery worker in runtime."""

    def __init__(self, env_path: str, app_name: str,
                 worker_name: str, tasks: dict, prefetch_multi: int,
                 cfg: WaldorfCfg, affinity: list):
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
        self.prefetch_multi = prefetch_multi
        self.cfg = cfg
        self.affinity = affinity

    def setup_logger(self, level):
        import logging
        log_path = get_path(
            'log', abspath=str(Path.home()) + '/.waldorf')
        return init_logger(self.app_name, log_path,
                           (level, logging.INFO), (True, False))

    def setup_tasks(self):
        """Set up all tasks."""
        for task_name, (task_code, opts) in self.tasks.items():
            # Execute the task code and get the function object.
            exec(task_code, globals(), locals())
            _code = locals()[task_name]
            self.logger.debug('Add task {}, {}'.format(
                task_name, _code))
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
        self.app.conf.worker_prefetch_multiplier = self.prefetch_multi
        self.logger.debug('Finish app configure.')
        self.setup_tasks()
        args = ['worker', '-n', self.worker_name, '-c', '1']
        if self.cfg.debug >= 2:
            args.append('-E')
        self.app.worker_main(args)
        self.logger.debug('end')


class TerminatorThread(threading.Thread):
    """Terminate celery worker using app control."""

    def __init__(self, up, app_name, worker_name,
                 cfg, event: threading.Event):
        super(TerminatorThread, self).__init__()
        self.up = up
        self.app_name = app_name
        self.worker_name = worker_name
        self.cfg = cfg
        self.event = event
        self.daemon = True

    def run(self):
        time.sleep(3)
        app = Celery(self.app_name,
                     broker=self.cfg.celery_broker,
                     backend=self.cfg.celery_backend)
        c = Control(app)
        self.up.up.logger.debug(
            c.ping(destination=[self.worker_name]))
        self.event.wait()
        c.shutdown(destination=[self.worker_name])


class Namespace(ClientNamespace):
    """Slave namespace."""

    def trigger_event(self, event, *args):
        self.log_event(event, 'enter')
        ret = super(Namespace, self).trigger_event(event, *args)
        self.log_event(event, 'leave')
        return ret

    def log_event(self, event, msg):
        if event in self.ignore_events:
            return
        self.up.logger.debug('{} {}'.format(msg, event))

    def setup(self, up: _WaldorfSio):
        self.up = up
        self.ignore_events = ['update_table']
        self.up.publ_info.ready = 'True'
        threading.Thread(target=self.update, daemon=True).start()

    def set_response(self, api, resp: Response):
        self.up.logger.debug(
            'Get response from {}'.format(resp.hostname))
        key = api + '_resp'
        self.up.pvte_info.responses[key].append(resp)
        key = api + '_count'
        self.up.pvte_info.events[key] -= 1
        if self.up.pvte_info.events[key] <= 0:
            self.up.pvte_info.events[api].set()

    def update(self):
        time.sleep(10)
        threading.Thread(target=self.update, daemon=True).start()

    def on_connect(self):
        # S2.2: Require for master information.
        self.emit(WaldorfAPI.GET_INFO)

    def on_sync(self, resp):
        resp = Response.decode(resp)
        self.up.pvte_info.set_sync(resp.resp)

    def on_get_info(self, resp):
        # S2.4: Send waldorf information back to master.
        resp = Response(self.up.publ_info.hostname,
                        0, self.up.publ_info)
        self.emit(WaldorfAPI.GET_INFO + '_resp', resp.encode())
        self.up.pvte_info.wait_sync(WaldorfAPI.GET_INFO)
        self.up.nb_queue.put((WaldorfAPI.SYNC, 0))

    def on_get_info_resp(self, resp):
        # S2.3: Receive master information
        # and put it into no-blocking queue.
        resp = Response.decode(resp)
        info = resp.resp
        assert isinstance(info, MasterPublInfo)
        self.up.remote_info = info
        self.up.put((WaldorfAPI.GET_INFO + '_resp', info))

    def on_reconnect(self):
        self.emit(WaldorfAPI.GET_INFO)

    def on_update_table(self, args):
        self.up.publ_info.update_load()
        resp = Response(self.up.publ_info.hostname,
                        0, self.up.publ_info)
        self.emit(WaldorfAPI.UPDATE_TABLE + '_resp',
                  resp.encode())

    def on_echo(self, resp):
        resp = Response.decode(resp)
        clt_sid = resp.clt_sid
        resp = Response(self.up.publ_info.hostname,
                        0, 0, clt_sid)
        self.emit(WaldorfAPI.ECHO + '_resp', resp.encode())

    def get_env(self, resp):
        """Set up virtual environment."""
        self.up.logger.debug('enter {}'.format(get_func_name()))
        resp = Response.decode(resp)
        uid, name, pairs, suites, cfg, restart = resp.resp
        key = uid + '_' + WaldorfAPI.GET_ENV
        self.up.pvte_info.pending_tasks.append(key)
        while self.up.pvte_info.busy > 0 and \
                self.up.pvte_info.pending_tasks[0] != key:
            time.sleep(0.2)
        self.up.pvte_info.pending_tasks.remove(key)
        self.up.pvte_info.busy += 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'

        info = self.up.pvte_info.ri[uid]
        info.sid = resp.clt_sid
        info.env_args = uid, name, pairs, suites
        info.env = WaldorfEnv(name, cfg, self.up.logger)

        _resp = info.env.get_env(pairs, suites)
        resp = Response(self.up.publ_info.hostname,
                        0, (_resp, restart), resp.clt_sid)
        self.emit(WaldorfAPI.GET_ENV + '_resp', resp.encode())

        self.up.pvte_info.busy -= 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'
        self.up.logger.debug('leave {}'.format(get_func_name()))

    def on_get_env(self, resp):
        threading.Thread(target=self.get_env,
                         args=(resp,), daemon=True).start()

    def on_reg_task(self, resp):
        resp = Response.decode(resp)
        uid, task_name, task_code, opts, restart = resp.resp

        info = self.up.pvte_info.ri[uid]
        info.tasks[task_name] = (task_code, opts)
        _resp = 0
        resp = Response(self.up.publ_info.hostname,
                        0, (_resp, restart), resp.clt_sid)
        self.emit(WaldorfAPI.REG_TASK + '_resp', resp.encode())

    def on_freeze(self, resp):
        """Freeze worker configuration and set up worker."""
        resp = Response.decode(resp)
        uid, hostname, prefetch_multi, restart = resp.resp
        key = uid + '_' + WaldorfAPI.FREEZE
        # Wait until other things finish
        self.up.pvte_info.pending_tasks.append(key)
        while self.up.pvte_info.busy > 0 and \
                self.up.pvte_info.pending_tasks[0] != key:
            time.sleep(0.2)
        self.up.pvte_info.pending_tasks.remove(key)
        self.up.pvte_info.busy += 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'

        info = self.up.pvte_info.ri[uid]
        info.hostname = hostname
        info.prefetch_multi = prefetch_multi
        args = [uid, info.env.get_py_path(),
                info.env.get_env_path(), info.tasks, info.prefetch_multi]
        info.worker_args = args
        self.up.pvte_info.clients.append(uid)

        _resp = 0
        resp = Response(self.up.publ_info.hostname,
                        0, (_resp, restart), resp.clt_sid)
        self.emit(WaldorfAPI.FREEZE + '_resp', resp.encode())

        self.up.pvte_info.busy -= 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'

    def on_assign_core(self, resp):
        resp = Response.decode(resp)
        # Wait until other things finish
        key = str(uuid.uuid4()) + '_' + WaldorfAPI.ASSIGN_CORE
        self.up.pvte_info.pending_tasks.append(key)
        while self.up.pvte_info.busy > 0 and \
                self.up.pvte_info.pending_tasks[0] != key:
            time.sleep(0.2)
        self.up.pvte_info.pending_tasks.remove(key)
        self.up.pvte_info.busy += 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'

        self.setup_workers(resp.resp)

        resp = Response(self.up.publ_info.hostname,
                        0, 'Success', resp.clt_sid)
        self.emit(WaldorfAPI.ASSIGN_CORE + '_resp', resp.encode())

        self.up.pvte_info.busy -= 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'

    def setup_workers(self, core_client):
        clt_list = []
        for idx, clt_uid in enumerate(core_client.values()):
            clt_list.append('{}: {}'.format(idx, clt_uid))
        self.up.logger.debug('Client list:\n' + '\n'.join(clt_list))

        changed_cores = []
        for core_uid in self.up.publ_info.cores_uid:
            if core_uid not in core_client:
                continue
            clt_uid = core_client[core_uid]
            if clt_uid != self.up.pvte_info.ci[core_uid].clt_uid:
                self.up.pvte_info.ci[core_uid].clt_uid = clt_uid
                changed_cores.append(core_uid)

        if not changed_cores:
            return

        self.terminate_workers(changed_cores)

        self.up.logger.info('Setup worker...(~4s)')
        for core_uid in changed_cores:
            clt_uid = core_client[core_uid]
            if clt_uid == '':
                continue
            info = self.up.pvte_info.ri[clt_uid]
            args = info.worker_args
            if args is None:
                self.up.logger.warning(
                    'Worker args is None, uid {}'.format(clt_uid))
                continue
            args = copy.deepcopy(args)
            args.extend([self.up.cfg, core_uid])
            self.setup_one_worker(args)
            self.up.pvte_info.ci[core_uid].clt_uid = clt_uid
        time.sleep(3.5)

    def setup_one_worker(self, args):
        """Set up Celery worker."""
        clt_uid, py_path, env_path, tasks, prefetch_multi, cfg, core_uid = args
        app_name = 'app-' + clt_uid
        affinity = self.up.publ_info.cores_uid.index(core_uid)
        worker_name = app_name.replace('-', '_') + '@{}-{}'.format(
            self.up.publ_info.hostname, affinity)
        self.up.logger.debug('Setup worker for uid: {}'
                             .format(clt_uid))
        mp.set_executable(py_path)
        w = CeleryWorker(env_path, app_name, worker_name,
                         tasks, prefetch_multi, cfg, affinity)
        w.start()
        time.sleep(0.1)
        # Affinity will be fixed
        os.system('taskset -pc {} {}'.format(str(affinity), w.pid))
        mp.set_executable(sys.executable)

        info = self.up.pvte_info.ci[core_uid]
        info.app_name = app_name
        info.worker_name = worker_name
        info.affinity = affinity
        info.worker = w

        event = threading.Event()
        info.terminator = event
        self.setup_app_control(app_name, worker_name, cfg, event)

    def setup_app_control(self, app_name, worker_name, cfg, event):
        TerminatorThread(
            self, app_name, worker_name, cfg, event).start()

    def terminate_workers(self, cores):
        try:
            _cores = []
            for core_uid in cores:
                info = self.up.pvte_info.ci[core_uid]
                worker = info.worker
                if worker is None:
                    continue
                _cores.append(core_uid)
            if not _cores:
                return
            self.up.logger.info('Terminate workers...(4s)')
            for core_uid in _cores:
                info = self.up.pvte_info.ci[core_uid]
                info.terminator.set()
            time.sleep(2)
            for core_uid in _cores:
                info = self.up.pvte_info.ci[core_uid]
                worker = info.worker
                assert isinstance(worker, mp.Process)
                worker.terminate()
                info.worker = None
            time.sleep(2)
        except:
            self.up.logger.error(
                'Terminate worker error. Exception: {}'.format(
                    traceback.format_exc()))

    def on_clean_up(self, resp):
        """Clean up and terminate Celery worker."""
        resp = Response.decode(resp)
        uid = resp.resp
        self.up.logger.debug('Request client uid: {}'.format(uid))

        if uid in self.up.pvte_info.ri:
            if uid in self.up.pvte_info.clients:
                self.up.logger.debug('Removing client uid: {}'
                                     .format(uid))
                self.up.pvte_info.clients.remove(uid)
                clients = '\n'.join(
                    [self.up.pvte_info.ri[uid].hostname
                     for uid in self.up.pvte_info.clients])
                self.up.logger.info(
                    '\nCurrent running client(s):\n{}'
                        .format(clients))
            self.up.pvte_info.ri.pop(uid, None)

        resp = Response(self.up.publ_info.hostname,
                        0, 'Success', resp.clt_sid)
        self.emit(WaldorfAPI.CLEAN_UP + '_resp', resp.encode())

    def on_change_core(self, resp):
        """Change core usage on runtime."""
        key = str(uuid.uuid4()) + '_' + WaldorfAPI.CHANGE_CORE
        self.up.pvte_info.pending_tasks.append(key)
        while self.up.pvte_info.busy > 0 and \
                self.up.pvte_info.pending_tasks[0] != key:
            time.sleep(0.2)
        self.up.pvte_info.pending_tasks.remove(key)
        self.up.pvte_info.busy += 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'
        resp = Response.decode(resp)
        core = resp.resp
        self.up.cfg.core = core
        self.up.publ_info.used_cores = core
        save_cfg('slave', self.up.cfg)
        self.up.nb_queue.put((WaldorfAPI.CHANGE_CORE, self.up.cfg))
        self.up.logger.info('Changed core to {}'.format(core))
        resp = Response(self.up.publ_info.hostname,
                        0, core, resp.clt_sid)
        self.emit(WaldorfAPI.CHANGE_CORE + '_resp', resp.encode())
        self.up.pvte_info.busy -= 1
        self.up.publ_info.ready = 'True' \
            if self.up.pvte_info.busy == 0 else 'False'

    def on_exit_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.EXIT, resp)
