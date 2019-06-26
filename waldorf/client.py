import multiprocessing as mp
import threading
import functools
import traceback
import logging
import inspect
import pickle
import base64
import queue
import uuid
import time

import billiard.exceptions
from celery import Celery
import redis

from socketio import Client
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import Crypto.Util.number

from waldorf.cfg import WaldorfCfg
from waldorf.common import *
from waldorf.util import *


class CeleryResultThread(threading.Thread):
    """Result thread. Collect results in a separate thread."""

    def __init__(self, up):
        super(CeleryResultThread, self).__init__()
        self.up = up
        assert isinstance(self.up, _WaldorfSio)
        self.res_q = self.up.result_q
        self.sio_q = self.up.sio_queue[0]
        self.cfg = self.up.cfg
        self.daemon = True
        self.client = redis.StrictRedis(host=self.cfg.broker_ip,
                                        port=self.cfg.redis_port)

    def run(self):
        assert isinstance(self.up, _WaldorfSio)
        task_list = []
        remove_list = []
        exit_flag = False
        while True:
            for i in range(self.res_q[0].qsize()):
                task_uid, r = self.res_q[0].get()
                if r == WaldorfAPI.EXIT:
                    exit_flag = True
                    break
                task_list.append((task_uid, r))
            if exit_flag:
                break
            if len(task_list) == 0:
                time.sleep(0.05)
                continue
            for task_uid, r in task_list:
                task_meta_id = 'celery-task-meta-' + r.id
                exist = self.client.exists(task_meta_id)

                if not exist:
                    now = time.time()
                    submit_time = self.up.pvte_info.ti[task_uid].submit_time
                    timeout = self.cfg.result_timeout - (now - submit_time)

                    if timeout >= 0:
                        time.sleep(0.05)
                        continue

                    # Register timeout tasks
                    if not self.cfg.retry_enable:
                        self.up.logger.warning('Task timeout ignore.')
                        # Return None when timeout
                        self.res_q[1].put((task_uid, None))
                        self.up.pvte_info.ti.pop(task_uid)
                        # Remove retrying task and restore retry flag
                        if task_uid in self.up.pvte_info.retry_tasks:
                            self.up.pvte_info.retry_tasks.remove(task_uid)
                            if len(self.up.pvte_info.retry_tasks) == 0 and \
                                    self.up.pvte_info.retry_flag:
                                self.up.pvte_info.retry_flag = False
                        # Add result to remove list
                        remove_list.append((task_uid, r))
                        self.up.pvte_info.sema.release()
                        continue

                    if not self.handle_retry(
                            task_uid, r, remove_list, etype='Timeout'):
                        exit_flag = True
                        break
                    continue

                try:
                    res = self.client.get(task_meta_id)
                    res = pickle.loads(res)
                    if res['traceback']:
                        if not isinstance(
                                res['traceback'], redis.ConnectionError):
                            raise Exception(res['traceback'])
                        if not self.handle_retry(
                                task_uid, r, remove_list,
                                etype=type(res['traceback'])):
                            exit_flag = True
                            break
                        continue

                    res = res['result']
                    if isinstance(
                            res, (redis.ConnectionError,
                                  billiard.exceptions.WorkerLostError)):
                        if not self.handle_retry(
                                task_uid, r, remove_list, etype=type(res)):
                            exit_flag = True
                            break
                        continue

                    # Receive result and put it into queue
                    self.res_q[1].put((task_uid, res))
                    self.up.pvte_info.ti.pop(task_uid)

                    # Remove retrying task and restore retry flag
                    if task_uid in self.up.pvte_info.retry_tasks:
                        self.up.pvte_info.retry_tasks.remove(task_uid)
                        if len(self.up.pvte_info.retry_tasks) == 0 and \
                                self.up.pvte_info.retry_flag:
                            self.up.pvte_info.retry_flag = False
                    # Add result to remove list
                    remove_list.append((task_uid, r))
                    self.up.pvte_info.sema.release()
                except:
                    # catch any exceptions and print it
                    print('L{}: {}'.format(
                        get_linenumber(), traceback.format_exc()))
                    # after that clean up slave
                    self.up.on_exit()
                    exit_flag = True
                    break

            for task_uid, r in remove_list:
                task_list.remove((task_uid, r))
            remove_list.clear()

    def handle_retry(self, task_uid, r, remove_list, etype=''):
        assert isinstance(self.up, _WaldorfSio)
        _ti = self.up.pvte_info.ti[task_uid]
        _ti.retry_times += 1
        retry_times = _ti.retry_times
        # Enable retry flag when any task already retried more than expected
        if retry_times >= self.cfg.retry_times // 2 and \
                task_uid not in self.up.pvte_info.retry_tasks:
            self.up.pvte_info.retry_tasks.append(task_uid)
            if not self.up.pvte_info.retry_flag:
                # Enable when any task already retried more than expected
                # This will disable new task submit until the lock is released
                self.up.pvte_info.retry_flag = True
                self.up.logger.warning('Retry flag enable.')
        task_name, args = _ti.info
        if retry_times > self.cfg.retry_times:
            self.up.logger.error(
                'Maximum retry times reached. '
                'task_name: {}, uid: {}'.format(task_name, task_uid))
            print('L{}: Maximum retry times reached. Exit.'
                  .format(get_linenumber()))
            self.up.on_exit()
            return False
        self.up.logger.warning(
            'Receive {}. Resend task. '
            'retry_times: {}, task_name: {}, uid: {}'
                .format(etype, retry_times, task_name, task_uid))
        remove_list.append((task_uid, r))
        r = self.up.pvte_info.ri.task_handlers[task_name].apply_async(
            args=(args,))
        _ti.submit_time = time.time()
        self.up.result_q[0].put((task_uid, r))
        return True


class SockWaitThread(threading.Thread):
    def __init__(self, up):
        super(SockWaitThread, self).__init__()
        self.up = up
        self.daemon = True

    def run(self):
        assert isinstance(self.up, _WaldorfSio)
        self.up.pvte_info.sio.wait()


class AdjustLimitThread(threading.Thread):
    def __init__(self, up):
        super(AdjustLimitThread, self).__init__()
        self.up = up
        assert isinstance(self.up, _WaldorfSio)
        self.sio_q = self.up.sio_queue[0]
        self.limit = 0
        self.last_retry_flag = False
        self.daemon = True

    @property
    def limit_and_reason(self):
        limit = 0
        reason = ''
        if self.up.pvte_info.retry_flag:
            if self.up.pvte_info.limit // 5 != limit // 5 and \
                    self.up.pvte_info.limit != 5:
                limit = max(self.up.pvte_info.sema.remain, 5)
                reason = 'retry flag enable'
            return limit, reason

        _limit = self.up.pvte_info.sema.remain + \
                 self.up.pvte_info.task_num

        if self.last_retry_flag:
            limit = _limit
            reason = 'retry flag disable'
            return limit, reason

        if _limit < self.up.pvte_info.limit != 5 and \
                self.up.pvte_info.limit // 5 != _limit // 5:
            limit = max(_limit, 5)
            reason = 'task num less than limit'
        return limit, reason

    def run(self):
        assert isinstance(self.up, _WaldorfSio)
        time.sleep(60)
        while True:
            limit, reason = self.limit_and_reason
            if limit > 0:
                self.limit = limit
                self.up.on_set_limit(self.limit, reason)
            self.last_retry_flag = self.up.pvte_info.retry_flag
            time.sleep(15)


class _WaldorfSio(mp.Process):
    """Handle client command in a separate process."""

    def __init__(self, sio_queue, submit_queue, nb_queue):
        super(_WaldorfSio, self).__init__()
        self.daemon = True
        self.sio_queue = sio_queue
        self.submit_queue = submit_queue
        self.nb_queue = nb_queue

        self.publ_info = ClientPublInfo()
        self.pvte_info = ClientPvteInfo()
        self.remote_info = MasterPublInfo()

    def setup(self):
        # S1: Setup socketio client.
        self.publ_info = self.sio_queue[0].get()
        self.cfg = self.sio_queue[0].get()

        assert isinstance(self.publ_info, ClientPublInfo)
        self.publ_info.cfg = self.cfg
        self.pvte_info.cfg = self.cfg
        self.ignore_events = ['submit']

        # S1.1: Setup logger.
        self.setup_logger()

        # S1.2: Connect to Waldorf master.
        from waldorf.namespace.client import Namespace
        self.logger.debug(
            'Connect to {}:{} with uid {}'.format(
                self.cfg.master_ip, self.cfg.waldorf_port,
                self.publ_info.uid))
        if self.cfg.debug >= 2:
            sio = Client(logger=self.logger)
        else:
            sio = Client()
        self.pvte_info.sio = sio
        ns = Namespace('/client')
        ns.setup(self)
        self.pvte_info.ns = ns
        sio.register_namespace(ns)
        sio.connect('http://{}:{}'.format(
            self.cfg.master_ip, self.cfg.waldorf_port))

        self.result_q = [queue.Queue(), self.submit_queue[0]]
        CeleryResultThread(self).start()

    def setup_logger(self):
        if self.cfg.debug >= 1:
            # Logging Waldorf client.
            self.logger = init_logger(
                'wd_client', get_path(relative_path='.'),
                (logging.DEBUG, logging.DEBUG))
        else:
            self.logger = DummyLogger()

    def put(self, r):
        self.sio_queue[1].put(r)

    def get_response(self, api, resp=None, count=-1):
        if count == -1:
            count = self.remote_info.slave_num
        key = api + '_count'
        self.pvte_info.events[key] = count + 1
        key = api + '_resp'
        self.pvte_info.responses[key] = []
        self.pvte_info.events[api] = threading.Event()
        if resp is None:
            self.pvte_info.ns.emit(api)
        else:
            assert isinstance(resp, Response)
            self.pvte_info.ns.emit(api, resp.encode())
        time.sleep(0.01)
        self.pvte_info.events[api].wait()
        self.pvte_info.events.pop(api, None)
        key = api + '_count'
        self.pvte_info.events.pop(key, None)
        response = self.pvte_info.responses[api + '_resp']
        return response

    def on_echo(self):
        """Send echo message."""
        resp = self.get_response(WaldorfAPI.ECHO)
        self.put(resp)

    def on_get_env(self, name, pairs, suites, cfg):
        """Send get environment message.

        This method will wait until all responses are received.
        """
        if cfg.env_cfg.git_credential is not None:
            args = cfg
            resp = Response(self.publ_info.hostname,
                            0, args)
            resp = self.get_response(
                WaldorfAPI.CHECK_GIT_C, resp, count=0)[0]
            if resp.code < 0:
                self.put((resp,))
                return

        args = (name, pairs, suites, cfg)
        self.pvte_info.ri.env_args = args

        args = (name, pairs, len(suites), cfg)
        resp = Response(self.publ_info.hostname,
                        0, args)
        self.get_response(WaldorfAPI.GET_ENV, resp, count=0)
        for i in range(len(suites)):
            args = suites[i]
            resp = Response(self.publ_info.hostname,
                            0, args)
            self.get_response(WaldorfAPI.GET_ENV, resp, count=0)
        resp = Response(self.publ_info.hostname,
                        0, 'Finished')
        resp = self.get_response(WaldorfAPI.GET_ENV, resp)
        self.logger.debug(resp)
        self.put(resp)

    def on_reg_task(self, task_name, task_code, opts):
        """Send register task message.

        Send task code to master server.
        """
        l = {}
        exec(task_code, {}, l)
        _code = l[task_name]

        self.pvte_info.ri.tasks[task_name] = (_code, opts)
        args = (task_name, task_code, opts)
        resp = Response(self.publ_info.hostname,
                        0, args)
        resp = self.get_response(WaldorfAPI.REG_TASK, resp)
        self.logger.debug(resp)
        self.put(resp)

    def on_freeze(self):
        """Send freeze message.

        Create new celery client and send freeze message.
        It will wait until all slaves are set up.
        """
        self.cfg.update()
        self.pvte_info.ri.app_name = 'app-' + self.publ_info.uid
        self.pvte_info.app = app = Celery(
            self.pvte_info.ri.app_name,
            broker=self.cfg.celery_broker,
            backend=self.cfg.celery_backend)
        app.conf.task_default_queue = self.pvte_info.ri.app_name
        app.conf.accept_content = ['json', 'pickle']
        app.conf.task_serializer = 'pickle'
        app.conf.result_serializer = 'pickle'
        app.conf.task_acks_late = True
        app.conf.worker_lost_wait = 60.0
        app.conf.result_expires = 1800
        for name, task in self.pvte_info.ri.tasks.items():
            _task = app.task(**task[1])(task[0])
            self.pvte_info.ri.task_handlers[name] = _task
            self.pvte_info.ri.task_handlers[name].ignore_result = True
        hostname = self.publ_info.hostname
        resp = Response(hostname, 0, self.cfg.prefetch_multi)
        resp = self.get_response(WaldorfAPI.FREEZE, resp, count=0)
        self.put(resp)

    def on_submit(self, task_uid, task_name, args):
        """Send the job to celery broker and use queue to get the result."""
        # Block until retry flag is False
        while self.pvte_info.retry_flag:
            time.sleep(0.1)
        self.pvte_info.sema.acquire()
        info = self.pvte_info.ti[task_uid]
        info.info = (task_name, args)
        r = self.pvte_info.ri.task_handlers[task_name].apply_async(
            args=(args,))
        self.result_q[0].put((task_uid, r))
        self.pvte_info.task_num -= 1
        self.put(0)

    def encrypt(self, cipher, info):
        """Resolve 'ValueError: Plaintext is too long.' in Crypto."""
        key_len = Crypto.Util.number.ceil_div(
            Crypto.Util.number.size(cipher._key.n), 8)
        length = key_len - 20
        cipher_text = b''
        for i in range(0, len(info), length):
            cipher_text += cipher.encrypt(info[i:i + length])
        cipher_text = base64.b64encode(cipher_text)
        return cipher_text

    def on_gen_git_c(self, info):
        """Generate git credential.

        Receive public key from server and encrypt information.
        """
        resp = self.get_response(WaldorfAPI.GEN_GIT_C, count=0)
        self._public_pem = Response.decode(resp[0]).resp
        rsa_key = RSA.importKey(self._public_pem)
        cipher = PKCS1_v1_5.new(rsa_key)
        info = pickle.dumps(info, -1)
        cipher_text = self.encrypt(cipher, info)
        self.put(cipher_text)

    def on_exit(self):
        """Send clean up and exit message."""
        self.result_q[0].put((0, WaldorfAPI.EXIT))
        resp = Response(self.publ_info.hostname,
                        0, self.publ_info.uid)
        self.get_response(WaldorfAPI.EXIT, resp, count=0)
        time.sleep(0.05)

    def on_set_limit(self, limit, reason):
        """Send get cores message."""
        self.logger.debug('Set limit to {} due to {}.'.format(
            limit, reason))
        if reason == 'set task num':
            self.pvte_info.task_num = limit
        hostname = self.publ_info.hostname
        resp = Response(hostname, 0, limit)
        self.pvte_info.ns.emit(WaldorfAPI.SET_LIMIT, resp.encode())

    def log_event(self, event, msg):
        if event in self.ignore_events:
            return
        self.logger.debug('{} {}'.format(msg, event))

    def get_handler(self, api, *args):
        """Just a way to automatically find handler method."""
        self.log_event(api, 'enter')
        handler_name = 'on_' + api
        if hasattr(self, handler_name):
            handler = getattr(self, handler_name)
            handler(*args)
        self.log_event(api, 'leave')

    def run(self):
        import signal
        # Avoid connection dropped when sending exit command
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.setup()
        SockWaitThread(self).start()
        AdjustLimitThread(self).start()

        while True:
            try:
                cmd = self.sio_queue[0].get()
                if cmd:
                    if cmd[0] != WaldorfAPI.SUBMIT:
                        self.logger.debug(
                            'Receive {} command.'.format(cmd[0]))
                    if cmd[1]:
                        self.get_handler(cmd[0], *cmd[1])
                    else:
                        self.get_handler(cmd[0])
                    if cmd[0] == WaldorfAPI.EXIT:
                        break
            except KeyboardInterrupt:
                self.logger.debug('Receive keyboard interrupt.')
                self.on_exit()
                break
            except Exception as e:
                self.on_exit()
                raise e
        self.pvte_info.sio.disconnect()
        self.logger.debug('Send signal to main process.')
        self.nb_queue.put((WaldorfAPI.EXIT, 0))
        self.logger.debug('Loop end')


class SioResultThread(threading.Thread):
    """Handling result from sio using a queue."""

    def __init__(self, up, nb_queue, events):
        super(SioResultThread, self).__init__()
        self.up = up
        self.nb_queue = nb_queue
        self.events = events
        self.daemon = True

    def run(self):
        assert isinstance(self.up, WaldorfClient)
        while self.up.running:
            resp, result = self.nb_queue.get()
            if resp == WaldorfAPI.GET_INFO:
                key = WaldorfAPI.GET_INFO
                assert isinstance(result, MasterPublInfo)
                self.up.remote_info = result
                self.events[key].set()
            if resp == WaldorfAPI.SET_LIMIT:
                key = WaldorfAPI.SET_LIMIT
                if key in self.events:
                    self.events[key].set()
            if resp == WaldorfAPI.SYNC:
                key = WaldorfAPI.SYNC
                self.events[key].set()
            if resp == WaldorfAPI.EXIT:
                key = WaldorfAPI.EXIT
                self.events[key].set()


class CallbackThread(threading.Thread):
    """Handling result using a queue.

    This is used for collecting results one by one.
    Because sometimes getting two results on the same time will cause some
    problems on Celery's tcp stream. So we add this to get results one by one.
    """

    def __init__(self, up, submit_q, callbacks):
        super(CallbackThread, self).__init__()
        self.up = up
        self.submit_q = submit_q
        self.callbacks = callbacks
        self.daemon = True

    def run(self):
        assert isinstance(self.up, WaldorfClient)
        while self.up.running:
            task_uid, result = self.submit_q[0].get()
            if self.callbacks[task_uid]:
                self.callbacks[task_uid](result=result)
            else:
                self.submit_q[1].put(result)
            self.callbacks.pop(task_uid, None)


# Create a decorator to handle exceptions in functions gracefully
def handle_with(*exceptions):
    def decorator(f):
        @functools.wraps(f)
        def func(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except exceptions as e:
                return args[0].interrupt_close(e)
            except Exception as e:
                raise e

        return func

    return decorator


class TaskState(object):
    IDLE = 0
    FREEZE = 1
    RUNNING = 2


class WaldorfClient(object):
    """Waldorf client"""

    def __init__(self, cfg: WaldorfCfg):
        """Setup client.

        Args:
            cfg: Configuration of Waldorf.
        """
        # S0: Setup client.
        self.cfg = cfg

        self._local_limit = self.cfg.submit_limit
        assert isinstance(self._local_limit, int), \
            'Limit should be int!'
        self.remote_info = None
        self.require_sync = False
        self.running = True
        self._task_num = 0
        self._task_state = TaskState.IDLE
        self._limit = 1 if self._local_limit == -1 \
            else self._local_limit

        # S0.1: Collect information.
        self.waldorf_info = ClientPublInfo()
        self.waldorf_info.cfg = self.cfg
        self.remote_info = MasterPublInfo()

        # S0.2: Setup information queues.
        self._sio_queue = [mp.Queue(), mp.Queue()]
        self._submit_queue = [mp.Queue(), mp.Queue()]
        self._sio_noblock_queue = mp.Queue()

        # S0.3: Use no-blocking queue to collect result from socketio process.
        self._events = {WaldorfAPI.EXIT: threading.Event()}
        SioResultThread(
            self, self._sio_noblock_queue, self._events).start()

        # S0.4: Setup socketio client.
        key = WaldorfAPI.GET_INFO
        self._events[key] = threading.Event()
        key = WaldorfAPI.SYNC
        self._events[key] = threading.Event()
        self._sio_queue[0].put(self.waldorf_info)
        self._sio_queue[0].put(self.cfg)
        self._sio_p = _WaldorfSio(self._sio_queue, self._submit_queue,
                                  self._sio_noblock_queue)
        self._sio_p.start()

        # S3: Setup rest parts.
        # S3.1: Wait until receive information from master.
        key = WaldorfAPI.GET_INFO
        self._events[key].wait()
        key = WaldorfAPI.SYNC
        self._events[key].wait()
        # Check version
        if self.remote_info.version != self.waldorf_info.version:
            print('Warning: Version mismatch! Local version: {}. '
                  'Master version: {}. Please reconfigure waldorf!'
                  .format(self.waldorf_info.version,
                          self.remote_info.version))
            self.running = False
            self._sio_p.terminate()
            raise Exception(
                'Version mismatch. Local version: {}.'
                ' Master version: {}.'
                    .format(self.waldorf_info.version,
                            self.remote_info.version)
            )

    def _setup_callback(self):
        self._callbacks = {}
        CallbackThread(
            self, self._submit_queue, self._callbacks).start()

    def _put(self, cmd: str, args=None):
        self._sio_queue[0].put((cmd, args))

    def _get(self):
        return self._sio_queue[1].get()

    @handle_with(KeyboardInterrupt, AssertionError)
    def echo(self):
        """Send echo message to test connections.

        This is just used for test.
        """
        assert self.remote_info.slave_num > 0, 'No slave available'
        self._put(WaldorfAPI.ECHO)
        return self._get()

    @handle_with(KeyboardInterrupt, AssertionError)
    def get_env(self, name, pairs, suites):
        """Setup environment.

        Check out example/gym_demo.py for usage.

        Args:
            name: Name of the environment.
            pairs: Command pairs.
            suites: Setup suites.

        Returns:
            list of slave responses. A response is consist of slave host name
            and response from the slave. A response from slave is consist of
            two part. First is exit code. If exit code is less than 0,
            it denotes an exception was raised. Second is the message.

        """
        # S4: Setup environment.
        assert self.remote_info.slave_num > 0, 'No slave available'
        self._put(WaldorfAPI.GET_ENV, (name, pairs, suites, self.cfg))
        resp = self._get()
        for _resp in resp:
            assert _resp.code == 0, _resp

    @handle_with(KeyboardInterrupt, AssertionError)
    def reg_task(self, task, opts=None):
        """Register task on slaves.

        Args:
            task: Function object.
            opts: Options to register this task. This options is used by Celery.

        """
        # S5: Register task.
        assert self.remote_info.slave_num > 0, 'No slave available'
        if opts is None:
            opts = {}
        # Right now it won't check if the task can be callable.
        task_name = task.__name__
        lines = inspect.getsourcelines(task)[0]
        lines[0] = lines[0].lstrip()
        task_code = '\n' + ''.join(lines)
        self._put(WaldorfAPI.REG_TASK, (task_name, task_code, opts))
        return self._get()

    @handle_with(KeyboardInterrupt, AssertionError)
    def freeze(self):
        """Freeze task configuration.

        This will do the actual task registration and start Celery worker.
        """
        # S6: Freeze.
        assert self.remote_info.slave_num > 0, 'No slave available'
        assert self._task_state == TaskState.IDLE, \
            'Can not freeze more than one time'
        self._put(WaldorfAPI.FREEZE)
        r = self._get()
        self._task_state = TaskState.FREEZE
        self._setup_callback()
        return r

    @handle_with(KeyboardInterrupt, AssertionError)
    def set_task_num(self, task_num):
        assert self._task_state >= TaskState.FREEZE, \
            'Run freeze first'
        self._task_num = task_num
        self._task_state = TaskState.RUNNING
        key = WaldorfAPI.SET_LIMIT
        self._events[key] = threading.Event()
        self._put(key, (task_num, 'set task num'))
        self._events[key].wait()
        self._events.pop(key)

    @handle_with(KeyboardInterrupt, AssertionError)
    def submit(self, task, args, callback=None, smooth=False):
        """Submit one job.

        Args:
            task: Function object.
            args: Arguments. This should be one serializable object.
            callback: If presents, callback will be called when result is ready.
            smooth: If true, program will wait for 0.01 sec after submission.

        Returns:
            If there is no callback, it will block and return the result.
            If callback presents, it will return None.

        """
        # S7: Submit task.
        assert self._task_state >= TaskState.FREEZE, \
            'Run freeze first'
        assert self._task_state >= TaskState.RUNNING, \
            'Set task number first'

        self._task_num -= 1
        assert self._task_num >= 0, 'Set the right task number'
        if self._task_num == 0:
            self._task_state = TaskState.FREEZE

        task_uid = str(uuid.uuid4())
        if callback:
            if smooth:
                time.sleep(0.01)
            self._callbacks[task_uid] = callback
            self._put(WaldorfAPI.SUBMIT,
                      (task_uid, task.__name__, args))
            self._get()
        else:
            self._callbacks[task_uid] = None
            self._put(WaldorfAPI.SUBMIT,
                      (task_uid, task.__name__, args))
            self._get()
            return self._submit_queue[1].get()

    @handle_with(KeyboardInterrupt, AssertionError)
    def test_submit(self, task, args, callback=None, smooth=False):
        """Test submission.

        This is only used for test.
        """
        assert self._task_state >= TaskState.FREEZE, \
            'Run freeze first'
        if callback:
            result = task(args)
            callback(result)
        else:
            return task(args)

    @handle_with(KeyboardInterrupt)
    def join(self):
        """Block until all result is handled by callback function.
         """
        while len(self._callbacks.keys()) != 0:
            time.sleep(0.1)

    @handle_with(KeyboardInterrupt)
    def generate_git_credential(self, info: dict):
        """Generate git credential file.

        This is used for avoiding put any sensitive information
        in the project folder.
        Check out example/gen_git_c_demo.py for usage.

        Args:
            info: git credential information

        Returns:
            bytes: Encrypted git credential information
        """
        self._put(WaldorfAPI.GEN_GIT_C, (info,))
        return self._get()

    def interrupt_close(self, e):
        """Clean up while catching keyboard interrupt"""
        if not self._events[WaldorfAPI.EXIT].is_set():
            self._put(WaldorfAPI.EXIT)
            self._events[WaldorfAPI.EXIT].wait()
        raise e

    def close(self):
        """Clean up while closing."""
        if not self._events[WaldorfAPI.EXIT].is_set():
            self._put(WaldorfAPI.EXIT)
            self._events[WaldorfAPI.EXIT].wait()
