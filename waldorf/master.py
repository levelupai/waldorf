from aiohttp import web
import socketio
import multiprocessing as mp
import http.cookies
import time
import waldorf
from waldorf import _WaldorfAPI
from waldorf.util import DummyLogger, init_logger, \
    get_path, ColoredFormatter, obj_decode, obj_encode
import logging
import logging.handlers
from waldorf.cfg import WaldorfCfg
import argparse
import asyncio
from Crypto import Random
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
import os
import base64
import pickle
import waldorf.md_util as md_util
from pathlib import Path
import datetime
import json
import sys


class _WaldorfWebApp(mp.Process):
    def __init__(self, cfg: WaldorfCfg, cmd_queue):
        super(_WaldorfWebApp, self).__init__()
        self.daemon = True
        self.cfg = cfg
        self.cmd_queue = cmd_queue
        self.debug = cfg.debug

    def setup(self):
        self.up_time = time.time()
        self.setup_logger()

        # async server setup
        self.sio = socketio.AsyncServer(async_mode='aiohttp')
        self.app = web.Application()
        self.sio.attach(self.app)
        self.env_path = sys.executable[:-11]
        self.waldorf_path = self.env_path + '/lib/python3.6/site-packages' \
                                            '/waldorf'
        self.app.router.add_static('/static', self.waldorf_path + '/static')
        self.app.router.add_get('/', self.index)
        self.client_ns = ClientNamespace('/client')
        self.client_ns.setup(self)
        self.slave_ns = SlaveNamespace('/slave')
        self.slave_ns.setup(self)
        self.admin_ns = AdminNamespace('/admin')
        self.admin_ns.setup(self)
        self.sio.register_namespace(self.client_ns)
        self.sio.register_namespace(self.slave_ns)
        self.sio.register_namespace(self.admin_ns)

        self.info = {}
        self.events = {}
        self.setup_rsa()

        # index is using js now, so this part is deprecated.
        self.mg = md_util.MarkdownGenerator()
        self.mg.add_element(md_util.Head(1, 'Waldorf Master Server'))
        self.mg.add_element(md_util.Text(''))
        self.mg.add_element(md_util.Head(2, 'Table of Connections'))
        self.md_table = md_util.Table()
        self.md_table.set_head(
            ['Hostname', 'Type', 'State', 'ConnectTime', 'DisconnectTime',
             'UID', 'Version', 'IP', 'CPU', 'CORES', 'USED_CORES',
             'Memory', 'OS'])
        self.mg.add_element(self.md_table)

    def setup_rsa(self):
        """Setup public key and private key for git credential."""
        cfg_path = get_path('config', abspath=str(Path.home()) + '/.waldorf')
        public_pem_path = cfg_path + '/public.pem'
        private_pem_path = cfg_path + '/private.pem'
        if not os.path.exists(public_pem_path) or \
                not os.path.exists(private_pem_path):
            random_generator = Random.new().read
            rsa = RSA.generate(1024, random_generator)
            self._private_pem = rsa.exportKey()
            self._public_pem = rsa.publickey().exportKey()
            with open(cfg_path + '/private.pem', 'wb') as f:
                f.write(self._private_pem)
            with open(cfg_path + '/public.pem', 'wb') as f:
                f.write(self._public_pem)
        with open(cfg_path + '/private.pem', 'rb') as f:
            self._private_pem = f.read()
        with open(cfg_path + '/public.pem', 'rb') as f:
            self._public_pem = f.read()
        self._private_key = RSA.importKey(self._private_pem)
        self._private_cipher = PKCS1_v1_5.new(self._private_key)
        self.random_generator = Random.new().read

    def setup_logger(self):
        if self.debug >= 2:
            path = get_path('log', abspath=str(Path.home()) + '/.waldorf')
            # logging async server
            _nf = ['[%(asctime)s]',
                   '[%(name)s]',
                   '[%(filename)20s:%(funcName)15s:%(lineno)5d]',
                   '[%(levelname)s]',
                   ' %(message)s']
            _cf = ['$GREEN[%(asctime)s]$RESET',
                   '[%(name)s]',
                   '$BLUE[%(filename)20s:%(funcName)15s:%(lineno)5d]$RESET',
                   '[%(levelname)s]',
                   ' $CYAN%(message)s$RESET']
            nformatter = logging.Formatter('-'.join(_nf))
            cformatter = ColoredFormatter('-'.join(_cf))
            root = logging.getLogger()
            root.setLevel(logging.DEBUG)
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(cformatter)
            rf = logging.handlers.RotatingFileHandler(path + '/wd_master.log',
                                                      maxBytes=50 * 1024 * 1024,
                                                      backupCount=5)
            rf.setFormatter(nformatter)
            root.addHandler(ch)
            root.addHandler(rf)
        if self.debug >= 1:
            # logging Waldorf master
            self.logger = init_logger(
                'wd_master',
                get_path('log', abspath=str(Path.home()) + '/.waldorf'),
                (logging.DEBUG, logging.DEBUG))
        else:
            self.logger = DummyLogger()

    async def index(self, request):
        """Serve the client-side application."""
        if self.cmd_queue.qsize() != 0:
            cmd = self.cmd_queue.get_nowait()
            self.text = cmd
        with open(self.waldorf_path + '/static/index.html') as f:
            return web.Response(text=f.read(), content_type='text/html')

    def run(self):
        # run server
        self.setup()
        web.run_app(self.app, port=self.cfg.waldorf_port)


class AdminNamespace(socketio.AsyncNamespace):
    """Namespace for management.

    This namespace is used to display information on index.html.
    """

    def setup(self, up: _WaldorfWebApp):
        """Setup namespace.

         Set up as parent process,
         using up to transfer information between namespace.
         """
        self.up = up
        self.info = {}
        self.info['change_core'] = {}
        self.info['change_core_resp'] = {}
        self.events = {}

    async def on_get_info(self, sid):
        """API for getting information.

        Get information of Waldorf slaves and clients.
        """
        resp = self.up.md_table.to_dict()
        await self.emit(_WaldorfAPI.GET_INFO + '_resp',
                        resp, room=sid)

    async def on_change_core(self, sid, args):
        """API for changing used core.

        Change the used cores of the given uid.
        """
        uid, core = args
        self.up.logger.info('on_change_core {} {}'.format(uid, core))
        if uid in self.up.slave_ns.info['uid']:
            _slave_sid = self.up.slave_ns.info['uid'][uid]['sid']
            self.info['change_core'][uid] = core
            self.events['change_core'] = asyncio.Event()
            await self.up.slave_ns.emit(_WaldorfAPI.CHANGE_CORE,
                                        core, room=_slave_sid)
            await self.events['change_core'].wait()
            resp = self.info['change_core_resp'][uid]
            if resp[0] == 0:
                await self.emit(_WaldorfAPI.CHANGE_CORE + '_resp',
                                json.dumps([0, 'Success.']), room=sid)
            else:
                await self.emit(_WaldorfAPI.CHANGE_CORE + '_resp',
                                json.dumps([-1, resp[1]]),
                                room=sid)
        else:
            await self.emit(_WaldorfAPI.CHANGE_CORE + '_resp',
                            json.dumps([-1, 'Failed to find uid.']), room=sid)

    async def on_check_ver(self, sid):
        """API for getting master version."""
        await self.emit(_WaldorfAPI.CHECK_VER + '_resp',
                        waldorf.__version__, room=sid)

    async def on_up_time(self, sid):
        """API for getting master up time."""
        await self.emit(_WaldorfAPI.UP_TIME + '_resp',
                        time.time() - self.up.up_time, room=sid)


class ClientNamespace(socketio.AsyncNamespace):
    """Waldorf client namespace."""

    def setup(self, up: _WaldorfWebApp):
        """Setup namespace.

        Set up as parent process,
        using up to transfer information between namespace.
        """
        self.up = up
        self.lost_timeout = 120
        self.info = {}
        self.info['sid'] = {}
        self.info['uid'] = {}
        self.connections = {}
        self.exit_dict = {}
        self.properties = {}
        asyncio.ensure_future(self.update())

    async def update(self):
        """Check connections every 5 seconds.

        If one client disconnected over 120 seconds,
        the master server will automatically do cleaning up for the client.
        """
        await asyncio.sleep(5)
        # Deal with disconnection.
        now = time.time()
        keys = list(self.info['uid'].keys())
        for k in keys:
            if 'disconnect_time' in self.info['uid'][k] and now - \
                    self.info['uid'][k]['disconnect_time'] > self.lost_timeout:
                await self.clean_up(k)
        asyncio.ensure_future(self.update())

    async def clean_up(self, uid):
        """Send clean up message to slave."""
        self.up.logger.debug('clean_up')
        self.up.logger.debug(
            'Connection lost over {} sec. Client uid: {} is removed'.format(
                self.lost_timeout, uid))
        self.info['uid'].pop(uid)
        await self.up.slave_ns.emit(_WaldorfAPI.CLEAN_UP,
                                    uid, room='slave')

    async def on_clean_up(self, sid, uid):
        """Receive client's clean up request."""
        self.up.logger.debug('on_clean_up')
        await self.up.slave_ns.emit(_WaldorfAPI.CLEAN_UP,
                                    uid, room='slave')

    async def on_connect(self, sid, environ):
        """Client connect.

        Collect information from the cookie and update server table.
        """
        self.up.logger.debug('on_connect')
        cookie = http.cookies.BaseCookie(environ['HTTP_COOKIE'])
        info = obj_decode(cookie['info'].value)
        uid = info['uid']
        hostname = info['hostname']
        version = info['ver']
        if uid not in self.info['uid']:
            self.info['uid'][uid] = {}
            self.up.logger.debug('client connect, hostname: {}, uid: {}'.
                                 format(hostname, uid))
        else:
            self.up.logger.debug('client reconnect, hostname: {}, uid: {}'.
                                 format(hostname, uid))
        if version != waldorf.__version__:
            self.up.logger.debug('Version mismatch. Local version: {}. '
                                 'Client version: {}.'
                                 .format(waldorf.__version__, version))
            await self.emit(_WaldorfAPI.VER_MISMATCH,
                            waldorf.__version__, room=sid)
        self.info['uid'][uid]['uid'] = uid
        self.info['uid'][uid]['connect_time'] = time.time()
        self.info['uid'][uid]['connect_time_readable'] = \
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.info['uid'][uid]['sid'] = sid
        self.info['uid'][uid]['hostname'] = hostname
        self.info['uid'][uid]['environ'] = environ
        self.info['sid'][sid] = uid
        self.connections[uid] = hostname
        self.properties[uid] = {
            'Hostname': hostname,
            'Type': 'Client',
            'State': 'Online',
            'ConnectTime': self.info['uid'][uid]['connect_time_readable'],
            'DisconnectTime': ' ',
            'UID': uid,
            'Version': version,
            'IP': info['ip'],
            'CPU': info['cpu_type'],
            'CORES': info['cpu_count'],
            'USED_CORES': ' ',
            'Memory': info['mem'],
            'OS': info['os']
        }
        # Use _c to denote client
        self.up.md_table.update_object(uid + '_c', self.properties[uid])

    async def on_check_ver(self, sid, version):
        """Receive client's check version request."""
        if version != waldorf.__version__:
            self.up.logger.debug('Version mismatch. Local version: {}. '
                                 'Client version: {}.'
                                 .format(waldorf.__version__, version))
        await self.emit(_WaldorfAPI.CHECK_VER + '_resp',
                        waldorf.__version__, room=sid)

    def on_disconnect(self, sid):
        """Client disconnect.

        Update table and remove active connections.
        """
        self.up.logger.debug('on_disconnect')
        uid = self.info['sid'][sid]
        self.up.md_table.remove_object(uid + '_c')
        self.properties.pop(uid, None)
        self.info['sid'].pop(sid, None)
        if uid in self.info['uid']:
            if uid in self.exit_dict:
                self.up.logger.debug('client {} disconnect, uid: {}.'.format(
                    self.info['uid'][uid]['hostname'], uid))
                self.info['uid'].pop(uid)
            else:
                self.info['uid'][uid]['disconnect_time'] = time.time()
                self.up.logger.debug('client {} disconnect abnormally, uid: {}.'
                                     .format(self.info['uid'][uid]['hostname'],
                                             uid))
        self.connections.pop(uid, None)

    def on_exit(self, sid, uid):
        self.up.logger.debug('client {} exit safely, uid: {}'.format(
            self.info['uid'][uid]['hostname'], uid))
        self.exit_dict[uid] = 0

    async def on_echo(self, sid):
        """Echo message, just for test."""
        self.up.logger.debug('on_echo')
        await self.emit(_WaldorfAPI.ECHO + '_resp', 'master', room=sid)
        await self.up.slave_ns.emit(
            _WaldorfAPI.ECHO, sid, room='slave')

    async def on_check_slave(self, sid):
        """Check how many slaves are available."""
        self.up.logger.debug('on_check_slave')
        self.up.logger.debug(self.up.slave_ns.connections)
        await self.emit(_WaldorfAPI.CHECK_SLAVE + '_resp',
                        self.up.slave_ns.connections, room=sid)

    async def on_get_env(self, sid, args):
        """Receive client's get env request.

        Setup environment on slave. Git credential will be decoded on master.
        """
        self.up.logger.debug('on_get_env')
        uid = self.info['sid'][sid]
        name, pairs, suites, cfg = obj_decode(args)
        if cfg.env_cfg.git_credential is not None:
            try:
                cred = base64.b64decode(cfg.env_cfg.git_credential)
                decrypted = self.up._private_cipher.decrypt(
                    cred, self.up.random_generator)
                cfg.env_cfg.git_credential = pickle.loads(decrypted)
            except:
                await self.emit(_WaldorfAPI.GET_ENV + '_resp',
                                [(-1, 'Error while parsing Git credential.')])
                return
        args = (name, pairs, suites, cfg)
        args = obj_encode(args)
        await self.up.slave_ns.emit(
            _WaldorfAPI.GET_ENV, (uid, args), room='slave')
        self.info['uid'][uid]['get_env_count'] = \
            list(self.up.slave_ns.connections.values())
        self.info['uid'][uid]['get_env_resp'] = []

    async def on_reg_task(self, sid, args):
        """Register task on slave.

        Send task information to slave.
        """
        self.up.logger.debug('on_reg_task')
        uid, task_name, task_code, opts = args
        if 'task' not in self.info['uid'][uid]:
            self.info['uid'][uid]['task'] = []
        self.info['uid'][uid]['task'].append([task_name, task_code, opts])
        await self.up.slave_ns.emit(_WaldorfAPI.REG_TASK,
                                    (uid, task_name, task_code, opts),
                                    room='slave')

    async def on_freeze(self, sid, args):
        """Freeze slave tasks configuration."""
        self.up.logger.debug('on_freeze')
        uid = args
        await self.up.slave_ns.emit(_WaldorfAPI.FREEZE,
                                    (uid, sid), room='slave')

    async def on_gen_git_c(self, sid):
        """Generate git credential.

        Send public key to client.
        """
        self.up.logger.debug('on_gen_git_c')
        key = self.up._public_pem.decode()
        await self.emit(_WaldorfAPI.GEN_GIT_C + '_resp', key, room=sid)


class SlaveNamespace(socketio.AsyncNamespace):
    """Waldorf slave namespace."""

    def setup(self, up: _WaldorfWebApp):
        """Setup namespace.

        Set up as parent process,
        using up to transfer information between namespace.
        """
        self.up = up
        self.lost_timeout = 120
        self.info = {}
        self.info['sid'] = {}
        self.info['uid'] = {}
        self.connections = {}
        self.exit_dict = {}
        self.properties = {}
        asyncio.ensure_future(self.update())

    async def update(self):
        """Check connections every 5 seconds.

        If one slave disconnected over 120 seconds,
        the master server will automatically do cleaning up for the slave.
        """
        await asyncio.sleep(5)
        # Deal with disconnection
        now = time.time()
        keys = list(self.info['uid'].keys())
        for k in keys:
            if 'disconnect_time' in self.info['uid'][k] and now - \
                    self.info['uid'][k]['disconnect_time'] > self.lost_timeout:
                self.up.logger.debug(
                    'Connection lost over {} sec. Slave uid: {} is removed'.
                        format(self.lost_timeout, k))
                self.info['uid'].pop(k)
        asyncio.ensure_future(self.update())

    async def on_connect(self, sid, environ):
        """Slave connect.

        Collect information from the cookie and update server table.
        """
        self.up.logger.debug('on_connect')
        cookie = http.cookies.BaseCookie(environ['HTTP_COOKIE'])
        info = obj_decode(cookie['info'].value)
        uid = info['uid']
        hostname = info['hostname']
        version = info['ver']
        if uid not in self.info['uid']:
            self.info['uid'][uid] = {}
            self.up.logger.debug('slave connect, hostname: {}, uid: {}'.
                                 format(hostname, uid))
        else:
            self.up.logger.debug('slave reconnect, hostname: {}, uid: {}'.
                                 format(hostname, uid))
        if version != waldorf.__version__:
            self.up.logger.debug('Version mismatch. Local version: {}. '
                                 'Slave version: {}.'
                                 .format(waldorf.__version__, version))
            await self.emit(_WaldorfAPI.VER_MISMATCH,
                            waldorf.__version__, room=sid)
        self.info['uid'][uid]['connect_time'] = time.time()
        self.info['uid'][uid]['connect_time_readable'] = \
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.info['uid'][uid]['sid'] = sid
        self.info['uid'][uid]['hostname'] = hostname
        self.info['uid'][uid]['environ'] = environ
        self.info['sid'][sid] = uid
        self.enter_room(sid, 'slave')
        self.connections[uid] = hostname
        self.properties[uid] = {
            'Hostname': hostname,
            'Type': 'Slave',
            'State': 'Online',
            'ConnectTime': self.info['uid'][uid]['connect_time_readable'],
            'DisconnectTime': ' ',
            'UID': uid,
            'Version': version,
            'IP': info['ip'],
            'CPU': info['cpu_type'],
            'CORES': info['cpu_count'],
            'USED_CORES': info['cfg_core'],
            'Memory': info['mem'],
            'OS': info['os']
        }
        # Use _s to denote slave
        self.up.md_table.update_object(uid + '_s', self.properties[uid])

    async def on_check_ver(self, sid, version):
        """Receive slave's check version request."""
        if version != waldorf.__version__:
            self.up.logger.debug('Version mismatch. Local version: {}. '
                                 'Slave version: {}.'
                                 .format(waldorf.__version__, version))
        await self.emit(_WaldorfAPI.CHECK_VER + '_resp',
                        waldorf.__version__, room=sid)

    def on_disconnect(self, sid):
        """Client disconnect.

        Update table and remove active connections.
        It will not remove the slave from the table.
        it will only set the disconnect time to current time
        and the state to offline. So the user will know
        when the slave disconnected when they checkout the master index page.
        """
        uid = self.info['sid'][sid]
        self.info['sid'].pop(sid, None)
        if uid in self.exit_dict:
            self.up.logger.debug('slave {} disconnect, uid: {}.'.format(
                self.info['uid'][uid]['hostname'], uid))
            self.properties[uid]['State'] = 'Offline'
        else:
            self.up.logger.debug('slave {} disconnect abnormally, uid: {}.'
                                 .format(self.info['uid'][uid]['hostname'],
                                         uid))
            self.properties[uid]['State'] = 'Offline(Abnormally)'
        self.info['uid'][uid]['disconnect_time'] = time.time()
        self.info['uid'][uid]['disconnect_time_readable'] = \
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.properties[uid]['DisconnectTime'] = \
            self.info['uid'][uid]['disconnect_time_readable']
        self.up.md_table.update_object(uid + '_s', self.properties[uid])
        self.leave_room(sid, 'slave')
        self.connections.pop(uid, None)

    def on_exit(self, sid, uid):
        self.up.logger.debug('slave {} exit safely, uid: {}'.format(
            self.info['uid'][uid]['hostname'], uid))
        self.exit_dict[uid] = 0

    async def on_get_env_resp(self, sid, args):
        """Get environment response from slaves.

        It will send a response to client when all slaves reply their responses.
        """
        uid, hostname, resp = args
        self.up.logger.debug('on_get_env_resp')
        self.up.logger.debug('get response from {}'.format(
            self.info['uid'][self.info['sid'][sid]]['hostname']))
        if uid not in self.up.client_ns.info['uid']:
            return
        self.up.client_ns.info['uid'][uid]['get_env_count'].remove(hostname)
        self.up.client_ns.info['uid'][uid]['get_env_resp'].append((hostname,
                                                                   resp))
        self.up.logger.debug('remaining {}'.format(
            self.up.client_ns.info['uid'][uid]['get_env_count']))
        if len(self.up.client_ns.info['uid'][uid]['get_env_count']) <= 0:
            await self.up.client_ns.emit(
                _WaldorfAPI.GET_ENV + '_resp',
                self.up.client_ns.info['uid'][uid]['get_env_resp'],
                room=self.up.client_ns.info['uid'][uid]['sid'])

    async def on_echo_resp(self, sid, client_sid):
        """Echo response from slaves."""
        # TODO: move client side wait to master? like get_env
        self.up.logger.debug('on_echo_resp')
        self.up.logger.debug('get response from {}'.format(
            self.info['uid'][self.info['sid'][sid]]['hostname']))
        await self.up.client_ns.emit(
            _WaldorfAPI.ECHO + '_resp', 'slave_' + sid, room=client_sid)

    async def on_freeze_resp(self, sid, client_sid):
        """Freeze response from slaves."""
        # TODO: move client side wait to master? like get_env
        self.up.logger.debug('on_freeze_resp')
        self.up.logger.debug('get response from {}'.format(
            self.info['uid'][self.info['sid'][sid]]['hostname']))
        await self.up.client_ns.emit(
            _WaldorfAPI.FREEZE + '_resp', 'slave_' + sid, room=client_sid)

    def on_change_core_resp(self, sid, resp):
        uid = self.info['sid'][sid]
        self.up.admin_ns.info['change_core_resp'][uid] = resp
        if resp[0] == 0:
            self.properties[uid]['USED_CORES'] = \
                self.up.admin_ns.info['change_core'][uid]
            self.up.md_table.update_object(uid + '_s',
                                           self.properties[uid])
        self.up.admin_ns.events['change_core'].set()


class WaldorfMaster(object):
    def __init__(self, cfg: WaldorfCfg):
        # Use a queue to pass in commands from the command-line
        # to the Waldorf Master process.
        self.web_queue = mp.Queue(20)
        self.app = _WaldorfWebApp(cfg, self.web_queue)
        self.app.start()

    def loop(self):
        while True:
            cmd = input('cmd:\n')
            if cmd == 'exit':
                self.app.terminate()
                print('exiting')
                break
            else:
                self.web_queue.put(cmd)
        print('end')


def parse_args():
    cfg = WaldorfCfg()
    parser = argparse.ArgumentParser(description='Waldorf master')
    parser.add_argument('-p', '--port', type=int, default=cfg.waldorf_port)
    parser.add_argument('-d', '--debug', type=int, default=cfg.debug)
    args = parser.parse_args()
    cfg.waldorf_port = args.port
    cfg.debug = args.debug
    return cfg


if __name__ == '__main__':
    cfg = parse_args()
    master = WaldorfMaster(cfg)
    master.loop()
