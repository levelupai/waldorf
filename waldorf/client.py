from socketIO_client import SocketIO, SocketIONamespace
import multiprocessing as mp
import threading
import functools
import traceback
import logging
import inspect
import pickle
import base64
import socket
import queue
import uuid
import time
import sys

import billiard.exceptions
from celery import Celery
import redis

from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import Crypto.Util.number

from waldorf.util import DummyLogger, Dummytqdm, init_logger, get_path, \
    ColoredFormatter, get_system_info, obj_encode, get_local_ip
from waldorf.cfg import WaldorfCfg
from waldorf import _WaldorfAPI
from waldorf.threading_u import DSemaphore
import waldorf


class CeleryResultThread(threading.Thread):
    """Result thread. Collect results in a separate thread."""

    def __init__(self, up):
        super(CeleryResultThread, self).__init__()
        self.up = up
        self.res_q = self.up.result_q
        self.sio_q = self.up.sio_queue[0]
        self.cfg = self.up.cfg
        self.retrying_task = []
        self.daemon = True
        self.client = redis.StrictRedis(host=self.cfg.broker_ip,
                                        port=self.cfg.redis_port)

    def run(self):
        task_list = []
        remove_list = []
        flag = False
        while True:
            for i in range(self.res_q[0].qsize()):
                task_uid, r = self.res_q[0].get()
                if r == _WaldorfAPI.CLEAN_UP:
                    flag = True
                    break
                task_list.append((task_uid, r))
            if flag:
                break
            if len(task_list) == 0:
                time.sleep(0.05)
                continue
            for task_uid, r in task_list:
                task_meta_id = 'celery-task-meta-' + r.id
                exist = self.client.exists(task_meta_id)
                if exist:
                    try:
                        res = self.client.get(task_meta_id)
                        res = pickle.loads(res)
                        if res['traceback']:
                            if isinstance(res['traceback'],
                                          redis.ConnectionError):
                                if not self.handle_retry(
                                        task_uid, r, remove_list,
                                        etype=type(res['traceback'])):
                                    flag = True
                                    break
                            else:
                                raise Exception(res['traceback'])
                        else:
                            res = res['result']
                            if isinstance(
                                    res, (redis.ConnectionError,
                                          billiard.exceptions.WorkerLostError)):
                                if not self.handle_retry(
                                        task_uid, r, remove_list,
                                        etype=type(res)):
                                    flag = True
                                    break
                            else:
                                # Receive result and put it into queue
                                self.res_q[1].put((task_uid, res))
                                self.up.info['tasks'].pop(task_uid)
                                # Remove retrying task and restore retry flag
                                if task_uid in self.retrying_task:
                                    self.retrying_task.remove(task_uid)
                                    if len(self.retrying_task) == 0:
                                        self.up.retry_flag = False
                                # Add result to remove list
                                remove_list.append((task_uid, r))
                    except Exception as e:
                        # catch any exceptions and print it
                        print('L102: ' + traceback.format_exc())
                        # after that clean up slave
                        self.sio_q.put((_WaldorfAPI.CLEAN_UP, None))
                        flag = True
                        break
                else:
                    now = time.time()
                    submit_time = self.up.info['tasks'][task_uid]['submit_time']
                    timeout = self.cfg.result_timeout - (now - submit_time)
                    # Register timeout tasks
                    if timeout < 0:
                        if self.cfg.retry_enable:
                            if not self.handle_retry(
                                    task_uid, r, remove_list, etype='Timeout'):
                                flag = True
                                break
                        else:
                            self.up.logger.warning('Task timeout ignore.')
                            # Return None when timeout
                            self.res_q[1].put((task_uid, None))
                            self.up.info['tasks'].pop(task_uid)
                            # Remove retrying task and restore retry flag
                            if task_uid in self.retrying_task:
                                self.retrying_task.remove(task_uid)
                                if len(self.retrying_task) == 0:
                                    self.up.retry_flag = False
                            # Add result to remove list
                            remove_list.append((task_uid, r))
                    time.sleep(0.05)

            for task_uid, r in remove_list:
                task_list.remove((task_uid, r))
            remove_list.clear()

    def handle_retry(self, task_uid, r, remove_list, etype=''):
        self.up.info['tasks'][task_uid]['retry_times'] += 1
        # Enable retry flag when any task already retried more than expected
        if self.up.info['tasks'][task_uid]['retry_times'] >= \
                self.cfg.retry_times - 1 and \
                task_uid not in self.retrying_task:
            self.retrying_task.append(task_uid)
            self.up.retry_flag = True
        task_name, args = self.up.info['tasks'][task_uid]['info']
        if self.up.info['tasks'][task_uid]['retry_times'] \
                > self.cfg.retry_times:
            self.up.logger.error('Maximum retry times reached. '
                                 'task_name: {}, uid: {}'.format(task_name,
                                                                 task_uid))
            print('L150: Maximum retry times reached. Exit.')
            self.sio_q.put((_WaldorfAPI.CLEAN_UP, None))
            return False
        self.up.logger.warning(
            'Receive {}. Resend task. retry_times: {}, task_name: {}, uid: {}'.
                format(etype, self.up.info['tasks'][task_uid]['retry_times'],
                       task_name, task_uid))
        remove_list.append((task_uid, r))
        r = self.up.info['task_handlers'][task_name].apply_async(
            args=(args,))
        self.up.info['tasks'][task_uid][
            'submit_time'] = time.time()
        self.up.result_q[0].put((task_uid, r))
        return True


class SockWaitThread(threading.Thread):
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
                self.up.logger.debug('Index error. Connect to {}:{} with uid {}'
                                     .format(self.up.cfg.master_ip,
                                             self.up.cfg.waldorf_port,
                                             self.up.uid))
                self.up.sock = SocketIO(self.up.cfg.master_ip,
                                        self.up.cfg.waldorf_port)
                self.up.client_ns = self.up.sock.define(Namespace, '/client')
                self.up.client_ns.setup(self.up)


class _WaldorfSio(mp.Process):
    """Handle client command in a separate process."""

    def __init__(self, sio_queue, submit_queue, nb_queue):
        super(_WaldorfSio, self).__init__()
        self.daemon = True
        self.sio_queue = sio_queue
        self.submit_queue = submit_queue
        self.nb_queue = nb_queue
        self.alive = True

    def setup(self):
        # Generate uid for client.
        self.uid = str(uuid.uuid4())
        self.system_info = get_system_info()
        self.cfg = self.sio_queue[0].get()
        self.debug = self.cfg.debug
        self.setup_logger()
        self.result_q = [queue.Queue(), self.submit_queue[0]]
        self.client = redis.StrictRedis(host=self.cfg.broker_ip,
                                        port=self.cfg.redis_port)
        self.rt = CeleryResultThread(self)
        self.rt.start()
        # Enable when any task already retried more than expected
        # This will disable new task submit until the lock is released
        self.retry_flag = False

        # Collect information.
        self.waldorf_info = {'uid': self.uid,
                             'hostname': socket.gethostname(),
                             'ver': waldorf.__version__,
                             'ip': get_local_ip(),
                             'os': self.system_info.os,
                             'cpu_type': self.system_info.cpu_type,
                             'cpu_count': self.system_info.cpu_count,
                             'cfg_core': self.cfg.core,
                             'mem': self.system_info.mem,
                             'load_avg1': ' ',
                             'load_avg5': ' ',
                             'load_avg15': ' ',
                             'prefetch_multi': ' ',
                             'ready': ' '}

        # Connect to Waldorf master.
        self.sock = SocketIO(self.cfg.master_ip, self.cfg.waldorf_port)
        self.logger.debug('Connect to {}:{} with uid {}'.format(
            self.cfg.master_ip, self.cfg.waldorf_port, self.uid))
        self.client_ns = self.sock.define(Namespace, '/client')
        self.client_ns.setup(self)

        self.events = {}
        self.info = {}
        self.info['tasks'] = {}
        self._code = None

    def setup_logger(self):
        if self.debug >= 2:
            # Logging socketIO-client output.
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
        if self.debug >= 1:
            # Logging Waldorf client.
            self.logger = init_logger('wd_client',
                                      get_path(relative_path='.'),
                                      (logging.DEBUG, logging.DEBUG))
        else:
            self.logger = DummyLogger()

    def put(self, r):
        self.sio_queue[1].put(r)

    def on_echo(self):
        """Send echo message."""
        self.logger.debug('enter on_echo')
        self.info['echo_count'] = len(self.info['check_slave_resp'].keys()) + 1
        self.info['echo_resp'] = []
        self.events['echo'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.ECHO)
        time.sleep(0.01)
        self.events['echo'].wait()
        self.put(self.info['echo_resp'])
        self.logger.debug('leave on_echo')

    def on_check_ver(self):
        """Send check version message."""
        self.logger.debug('enter on_check_ver')
        self.events['check_ver'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.CHECK_VER, waldorf.__version__)
        time.sleep(0.01)
        self.events['check_ver'].wait()
        self.put(self.info['check_ver_resp'])
        self.logger.debug('leave on_check_ver')

    def on_get_cores(self):
        """Send get cores message."""
        self.logger.debug('enter on_get_cores')
        self.events['get_cores'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.GET_CORES)
        time.sleep(0.01)
        self.events['get_cores'].wait()
        self.events.pop('get_cores')
        self.put(self.info['get_cores_resp'])
        self.logger.debug('leave on_get_cores')

    def on_check_slave(self):
        """Send check slave message."""
        self.logger.debug('enter on_check_slave')
        self.events['check_slave'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.CHECK_SLAVE)
        time.sleep(0.01)
        self.events['check_slave'].wait()
        self.put(len(self.info['check_slave_resp'].keys()))
        self.logger.debug(self.info['check_slave_resp'])
        self.logger.debug('leave on_check_slave')

    def on_get_env(self, name, pairs, suites, cfg):
        """Send get environment message.

        This method will wait until all responses are received.
        """
        self.logger.debug('enter on_get_env')
        args = (name, pairs, suites, cfg)
        args = obj_encode(args)
        self.events['get_env'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.GET_ENV, args)
        time.sleep(0.01)
        self.events['get_env'].wait()
        self.logger.debug(self.info['get_env_resp'])
        self.put(self.info['get_env_resp'])
        self.logger.debug('leave on_get_env')

    def on_reg_task(self, task_name, task_code, opts):
        """Send register task message.

        Send task code to master server.
        """
        self.logger.debug('enter on_reg_task')
        l = {}
        exec(task_code, {}, l)
        self._code = l[task_name]
        self.info['tasks'][task_name] = [self._code, opts]
        self.client_ns.emit(_WaldorfAPI.REG_TASK, (self.uid, task_name,
                                                   task_code, opts))
        self.put(0)
        self.logger.debug('leave on_reg_task')

    def on_freeze(self):
        """Send freeze message.

        Create new celery client and send freeze message.
        It will wait until all slaves are set up.
        """
        self.logger.debug('enter on_freeze')
        self.cfg.update()
        self.app_name = 'app-' + self.uid
        self.info['app'] = app = Celery(self.app_name,
                                        broker=self.cfg.celery_broker,
                                        backend=self.cfg.celery_backend)
        app.conf.task_default_queue = self.app_name
        app.conf.accept_content = ['json', 'pickle']
        app.conf.task_serializer = 'pickle'
        app.conf.result_serializer = 'pickle'
        app.conf.task_acks_late = True
        app.conf.worker_lost_wait = 60.0
        app.conf.result_expires = 1800
        self.info['task_handlers'] = {}
        for name, task in self.info['tasks'].items():
            self.info['task_handlers'][name] = app.task(**task[1])(task[0])
            self.info['task_handlers'][name].ignore_result = True
        self.info['freeze_count'] = len(self.info['check_slave_resp'].keys())
        self.info['freeze_resp'] = []
        self.events['freeze'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.FREEZE, self.uid)
        time.sleep(0.01)
        self.events['freeze'].wait()
        self.put(0)
        self.logger.debug('leave on_freeze')

    def on_submit(self, task_uid, task_name, args):
        """Send the job to celery broker and use queue to get the result."""
        # Block until retry flag is False
        while self.retry_flag:
            time.sleep(0.1)
        self.info['tasks'][task_uid] = {}
        self.info['tasks'][task_uid]['retry_times'] = 0
        self.info['tasks'][task_uid]['info'] = (task_name, args)
        self.info['tasks'][task_uid]['submit_time'] = time.time()
        r = self.info['task_handlers'][task_name].apply_async(
            args=(args,))
        self.result_q[0].put((task_uid, r))
        self.put(0)

    def encrypt(self, cipher, info):
        """Resolve "ValueError: Plaintext is too long." in Crypto."""
        key_len = Crypto.Util.number.ceil_div(Crypto.Util.number.size(
            cipher._key.n), 8)
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
        self.logger.debug('enter on_gen_git_c')
        self.events['gen_git_c'] = threading.Event()
        self.client_ns.emit(_WaldorfAPI.GEN_GIT_C)
        time.sleep(0.01)
        self.events['gen_git_c'].wait()
        info = pickle.dumps(info, -1)
        self._public_pem = self.info['gen_git_c_resp'].encode()
        rsa_key = RSA.importKey(self._public_pem)
        cipher = PKCS1_v1_5.new(rsa_key)
        cipher_text = self.encrypt(cipher, info)
        self.put(cipher_text)

    def on_clean_up(self):
        """Send clean up and exit message."""
        self.logger.debug('enter on_clean_up')
        self.client_ns.emit(_WaldorfAPI.CLEAN_UP, self.uid)
        self.client_ns.emit(_WaldorfAPI.EXIT, self.uid)
        time.sleep(0.05)
        self.alive = False
        self.result_q[0].put((0, _WaldorfAPI.CLEAN_UP))
        self.logger.debug('leave on_clean_up')

    def get_handler(self, api):
        """Just a way to automatically find handler method."""
        return self.__getattribute__('on_' + api)

    def run(self):
        self.setup()
        SockWaitThread(self).start()
        while True:
            try:
                cmd = self.sio_queue[0].get()
                if cmd:
                    if cmd[1]:
                        self.get_handler(cmd[0])(*cmd[1])
                    else:
                        self.get_handler(cmd[0])()
                    if cmd[0] == _WaldorfAPI.CLEAN_UP:
                        break
            except KeyboardInterrupt:
                self.on_clean_up()
                break
            except Exception as e:
                self.on_clean_up()
                raise e
        self.sock.disconnect()
        self.logger.debug('loop end')


class Namespace(SocketIONamespace):
    def setup(self, up: _WaldorfSio):
        self.up = up
        self.emit(_WaldorfAPI.GET_INFO + '_resp',
                  obj_encode(self.up.waldorf_info))

    def on_reconnect(self):
        self.up.logger.debug('on_reconnect')
        self.emit(_WaldorfAPI.GET_INFO + '_resp',
                  obj_encode(self.up.waldorf_info))

    def on_echo_resp(self, resp):
        self.up.logger.debug('on_echo_resp')
        self.up.info['echo_resp'].append(resp)
        self.up.info['echo_count'] -= 1
        if self.up.info['echo_count'] <= 0:
            self.up.events['echo'].set()

    def on_get_env_resp(self, resp):
        self.up.logger.debug('on_get_env_resp')
        self.up.info['get_env_resp'] = resp
        self.up.events['get_env'].set()

    def on_check_slave_resp(self, resp):
        self.up.logger.debug('on_check_slave_resp')
        self.up.info['check_slave_resp'] = resp
        self.up.events['check_slave'].set()

    def on_get_cores_resp(self, resp):
        self.up.logger.debug('on_get_cores_resp')
        if 'get_cores' in self.up.events:
            self.up.info['get_cores_resp'] = resp
            self.up.events['get_cores'].set()
        else:
            self.up.nb_queue.put(('get_cores_resp', resp))

    def on_freeze_resp(self, resp):
        self.up.logger.debug('on_freeze_resp')
        self.up.info['freeze_resp'].append(resp)
        self.up.info['freeze_count'] -= 1
        if self.up.info['freeze_count'] <= 0:
            self.up.events['freeze'].set()

    def on_ver_mismatch(self, version):
        print('Warning: Version mismatch. Local version: {}. '
              'Master version: {}. Please reconfigure waldorf!'
              .format(waldorf.__version__, version))

    def on_check_ver_resp(self, version):
        self.up.logger.debug('on_check_ver_resp')
        self.up.info['check_ver_resp'] = version
        self.up.events['check_ver'].set()

    def on_gen_git_c_resp(self, resp):
        self.up.logger.debug('on_gen_git_c_resp')
        self.up.info['gen_git_c_resp'] = resp
        self.up.events['gen_git_c'].set()


class SioResultThread(threading.Thread):
    """Handling result from sio using a queue."""

    def __init__(self, up, nb_queue, sema):
        super(SioResultThread, self).__init__()
        self.up = up
        self.nb_queue = nb_queue
        self.sema = sema
        self.daemon = True

    def run(self):
        while True:
            resp, result = self.nb_queue.get()
            if resp == 'get_cores_resp':
                if self.up.limit == 0:
                    self.sema.set_value(result)


class CallbackThread(threading.Thread):
    """Handling result using a queue.

    This is used for collecting results one by one.
    Because sometimes getting two results on the same time will cause some
    problems on Celery's tcp stream. So we add this to get results one by one.
    """

    def __init__(self, submit_q, callbacks, sema):
        super(CallbackThread, self).__init__()
        self.submit_q = submit_q
        self.callbacks = callbacks
        self.sema = sema
        self.daemon = True

    def run(self):
        while True:
            task_uid, result = self.submit_q[0].get()
            if self.callbacks[task_uid]:
                self.callbacks[task_uid](result=result)
            else:
                self.submit_q[1].put(result)
            self.callbacks.pop(task_uid, None)
            if self.sema:
                self.sema.release()


# Create a decorator to handle exceptions in functions gracefully
def handle_with(*exceptions):
    def decorator(f):
        @functools.wraps(f)
        def func(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except exceptions as e:
                return args[0].interrupt_close()
            except Exception as e:
                raise e

        return func

    return decorator


class WaldorfClient(object):
    """Waldorf client"""

    def __init__(self, cfg: WaldorfCfg):
        """Setup client.

        Args:
            cfg: Configuration of Waldorf.
            limit: Limitation of submitting jobs simultaneously.
        """
        self.cfg = cfg
        self.limit = self.cfg.submit_limit
        self._sio_queue = [mp.Queue(), mp.Queue()]
        self._submit_queue = [mp.Queue(), mp.Queue()]
        self._sio_noblock_queue = mp.Queue()
        self._sio_queue[0].put(self.cfg)
        self._sio_p = _WaldorfSio(self._sio_queue, self._submit_queue,
                                  self._sio_noblock_queue)
        self._sio_p.start()
        self.callbacks = {}
        self._sema = None
        self.slave_num = None
        self.is_freeze = False
        version = self.check_ver()
        if version != waldorf.__version__:
            raise Exception('Version mismatch. Local version: {}. '
                            'Master version: {}.'
                            .format(waldorf.__version__, version))
        if self.limit > 0:
            self._sema = DSemaphore(self.limit)
        else:
            self._sema = DSemaphore(self.get_cores())
        SioResultThread(self, self._sio_noblock_queue, self._sema).start()
        CallbackThread(self._submit_queue, self.callbacks,
                       self._sema).start()

    def _put(self, cmd: str, args=None):
        self._sio_queue[0].put((cmd, args))

    def _get(self):
        return self._sio_queue[1].get()

    @handle_with(KeyboardInterrupt)
    def echo(self):
        """Send echo message to test connections.

        This is just used for test.
        """
        if self.slave_num is None:
            self.check_slave()
        assert self.slave_num > 0, 'No slave available'
        self._put(_WaldorfAPI.ECHO)
        return self._get()

    @handle_with(KeyboardInterrupt)
    def check_ver(self):
        """Request for checking waldorf version."""
        self._put(_WaldorfAPI.CHECK_VER)
        return self._get()

    @handle_with(KeyboardInterrupt)
    def get_cores(self):
        """Get total cores."""
        self._put(_WaldorfAPI.GET_CORES)
        return self._get()

    @handle_with(KeyboardInterrupt)
    def check_slave(self):
        """Check number of connected slaves.

        Waldorf will automatically check slaves
        before running any slave related API.

        Returns:
            Number of connected slaves
        """
        self._put(_WaldorfAPI.CHECK_SLAVE)
        self.slave_num = self._get()
        return self.slave_num

    @handle_with(KeyboardInterrupt)
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
        if self.slave_num is None:
            self.check_slave()
        assert self.slave_num > 0, 'No slave available'
        self._put(_WaldorfAPI.GET_ENV, (name, pairs, suites, self.cfg))
        return self._get()

    @handle_with(KeyboardInterrupt)
    def reg_task(self, task, opts=None):
        """Register task on slaves.

        Args:
            task: Function object.
            opts: Options to register this task. This options is used by Celery.

        """
        if self.slave_num is None:
            self.check_slave()
        assert self.slave_num > 0, 'No slave available'
        if opts is None:
            opts = {}
        # Right now it won't check if the task can be callable.
        task_name = task.__name__
        lines = inspect.getsourcelines(task)[0]
        lines[0] = lines[0].lstrip()
        task_code = '\n' + ''.join(lines)
        self._put(_WaldorfAPI.REG_TASK, (task_name, task_code, opts))
        return self._get()

    @handle_with(KeyboardInterrupt)
    def freeze(self):
        """Freeze task configuration.

        This will do the actual task registration and start Celery worker.
        """
        if self.slave_num is None:
            self.check_slave()
        assert self.slave_num > 0, 'No slave available'
        self._put(_WaldorfAPI.FREEZE)
        r = self._get()
        self.is_freeze = True
        return r

    @handle_with(KeyboardInterrupt)
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
        assert self.is_freeze, 'Run freeze first'
        task_uid = str(uuid.uuid4())
        if callback:
            if self._sema:
                self._sema.acquire()
            if smooth:
                time.sleep(0.01)
            self.callbacks[task_uid] = callback
            self._put(_WaldorfAPI.SUBMIT, (task_uid, task.__name__, args))
            self._get()
        else:
            self.callbacks[task_uid] = None
            self._put(_WaldorfAPI.SUBMIT, (task_uid, task.__name__, args))
            self._get()
            return self._submit_queue[1].get()

    @handle_with(KeyboardInterrupt)
    def test_submit(self, task, args, callback=None, smooth=False):
        """Test submission.

        This is only used for test.
        """
        if callback:
            result = task(args)
            callback(result)
        else:
            return task(args)

    @handle_with(KeyboardInterrupt)
    def join(self):
        """Block until all result is handled by the callback function."""
        while len(self.callbacks.keys()) != 0:
            time.sleep(0.1)

    @handle_with(KeyboardInterrupt)
    def generate_git_credential(self, info: dict):
        """Generate git credential file.

        This is used for avoiding put any sensitive information
        in the project folder. Check out example/gen_git_c_demo.py for usage.

        Args:
            info: git credential information

        Returns:
            bytes: Encrypted git credential information
        """
        self._put(_WaldorfAPI.GEN_GIT_C, (info,))
        return self._get()

    def interrupt_close(self):
        """Clean up while catching keyboard interrupt"""
        self._put(_WaldorfAPI.CLEAN_UP)
        time.sleep(0.1)
        raise KeyboardInterrupt

    def close(self):
        """Clean up while closing."""
        self._put(_WaldorfAPI.CLEAN_UP)
        time.sleep(0.1)
