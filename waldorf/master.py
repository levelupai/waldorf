import multiprocessing as mp
from pathlib import Path
import threading
import traceback
import argparse
import base64
import time
import os

from aiohttp import web
import socketio

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto import Random
import Crypto.Util.number

from waldorf.cfg import WaldorfCfg, load_cfg, save_cfg
import waldorf.md_util as md_util
from waldorf.common import *
from waldorf.util import *


class CommandThread(threading.Thread):
    def __init__(self, up):
        super(CommandThread, self).__init__()
        self.up = up
        self.daemon = True

    def run(self):
        assert isinstance(self.up, _WaldorfWebApp)
        while self.up.alive:
            if self.up.cmd_queue.qsize() == 0:
                time.sleep(0.1)
                continue
            try:
                cmd = self.up.cmd_queue.get_nowait()
                if cmd == 'info':
                    self.up.logger.info(
                        '\n' + self.up.publ_info.to_table())
            except:
                traceback.print_exc()


class _WaldorfWebApp(mp.Process):
    def __init__(self, cfg: WaldorfCfg, cmd_queue):
        super(_WaldorfWebApp, self).__init__()
        self.daemon = True
        self.cfg = cfg
        self.cmd_queue = cmd_queue
        self.publ_info = MasterPublInfo()
        self.pvte_info = MasterPvteInfo()

    def setup(self):
        # S2: Setup socketio server.
        # S2.1: Setup logger.
        self.setup_logger()

        # S2.2: Collect information.
        self.publ_info = MasterPublInfo()
        self.publ_info.record_up_time()
        self.pvte_info = MasterPvteInfo()
        self.publ_info.cfg = self.cfg
        self.pvte_info.cfg = self.cfg

        self.setup_sio_server()
        self.setup_rsa()
        self.setup_table()

        self.alive = True
        CommandThread(self).start()

        # info for restart slave task
        self._temp_reg_info = {}
        self.reg_info = {}
        self.registered_info = {}

    def setup_sio_server(self):
        from waldorf.namespace.master.client import ClientNamespace
        from waldorf.namespace.master.slave import SlaveNamespace
        from waldorf.namespace.master.admin import AdminNamespace
        # S2.3: Setup async server.
        if self.cfg.debug >= 2:
            sio = socketio.AsyncServer(
                logger=self.logger, async_mode='aiohttp')
        else:
            sio = socketio.AsyncServer(async_mode='aiohttp')
        self.app = web.Application()
        sio.attach(self.app)
        self.app.router.add_static(
            '/static',
            self.publ_info.waldorf_path + '/static')
        self.app.router.add_get('/', self.index)
        ns = AdminNamespace('/admin')
        ns.setup(self)
        self.pvte_info.adm_ns = ns
        ns = SlaveNamespace('/slave')
        ns.setup(self)
        self.pvte_info.slv_ns = ns
        ns = ClientNamespace('/client')
        ns.setup(self)
        self.pvte_info.clt_ns = ns
        sio.register_namespace(self.pvte_info.adm_ns)
        sio.register_namespace(self.pvte_info.slv_ns)
        sio.register_namespace(self.pvte_info.clt_ns)

    def setup_table(self):
        table = md_util.Table()
        table.set_header(
            [
                TableHeader.HOSTNAME, TableHeader.TYPE,
                TableHeader.STATE,
                TableHeader.CONN_TIME, TableHeader.DISCONN_TIME,
                TableHeader.UID, TableHeader.VERSION,
                TableHeader.IP, TableHeader.CPU,
                TableHeader.MEMORY, TableHeader.OS,
            ])
        self.pvte_info.clt_tbl = table
        table = md_util.Table()
        table.set_header(
            [
                TableHeader.HOSTNAME, TableHeader.TYPE,
                TableHeader.STATE,
                TableHeader.CONN_TIME, TableHeader.DISCONN_TIME,
                TableHeader.UID, TableHeader.VERSION,
                TableHeader.IP, TableHeader.READY,
                TableHeader.CORES, TableHeader.USED,
                TableHeader.LOAD_PER, TableHeader.TOTAL_PER,
                TableHeader.LOAD_1, TableHeader.LOAD_5,
                TableHeader.LOAD_15,
                TableHeader.CPU, TableHeader.MEMORY, TableHeader.OS,
            ])
        self.pvte_info.slv_tbl = table

    def setup_rsa(self):
        """Setup public key and private key for git credential."""
        cfg_path = get_path(
            'rsa', abspath=str(Path.home()) + '/.waldorf')
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

    def decrypt(self, info):
        """Resolve "ValueError: Plaintext is too long." in Crypto."""
        info = base64.b64decode(info)
        key_len = Crypto.Util.number.ceil_div(
            Crypto.Util.number.size(self._private_cipher._key.n), 8)
        decrypted = b''
        for i in range(0, len(info), key_len):
            decrypted += self._private_cipher.decrypt(
                info[i:i + key_len], self.random_generator)
        return decrypted

    def setup_logger(self):
        log_path = get_path(
            'log', abspath=str(Path.home()) + '/.waldorf')
        if self.cfg.debug >= 1:
            # logging Waldorf master
            import logging
            self.logger = init_logger(
                'wd_master', log_path, (logging.DEBUG, logging.DEBUG))
        else:
            import logging
            self.logger = init_logger(
                'wd_master', log_path, (logging.INFO, logging.DEBUG))

    async def index(self, request):
        """Serve the client-side application."""
        with open(self.publ_info.waldorf_path +
                  '/static/index.html') as f:
            return web.Response(
                text=f.read(), content_type='text/html')

    def run(self):
        # Run server
        self.setup()
        web.run_app(self.app, port=self.cfg.waldorf_port)


class WaldorfMaster(object):
    def __init__(self, cfg: WaldorfCfg):
        # S1: Start socketio server.
        # Use a queue to pass in commands from the command-line
        # to the Waldorf Master process.
        self.web_queue = mp.Queue(20)
        self.app = _WaldorfWebApp(cfg, self.web_queue)
        self.app.start()

    def loop(self):
        time.sleep(2)
        while True:
            cmd = input('cmd:\n')
            if cmd == 'exit':
                self.app.terminate()
                print('L{}: Exiting'.format(get_linenumber()))
                break
            else:
                self.web_queue.put(cmd)
        print('L{}: End'.format(get_linenumber()))


def parse_args():
    # S0: Load configuration.
    _cfg = load_cfg('master')
    parser = argparse.ArgumentParser(description='Waldorf master')
    parser.add_argument('-p', '--port', type=int,
                        default=_cfg.waldorf_port)
    parser.add_argument('-d', '--debug', type=int, default=_cfg.debug)
    args = parser.parse_args()
    _cfg.waldorf_port = args.port
    _cfg.debug = args.debug
    save_cfg('master', _cfg)
    return _cfg


if __name__ == '__main__':
    print('''
░░░░░░░░░░░░░░░░░░░░░░░░░░░░████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░░░░██████████░███░░░░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░██████████░░░███████░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░██████████░░░████░████░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░█████████░░░████░░██████░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░████████░░░░███░░░█████░██░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░██████░░░░███░░░████░░░███░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░██████░░░░░░░░░███░░░█████░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░██████░░░░░░░████░░░██████░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░██████░░░░░░░░░░░░░███████░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░██████░░░░░░░░░░████████░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░███░░░█░░░░░░█████████░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░░░░█████████████████░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░░░█████████████████░░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░░░░░████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
░░░░░░░░░░░░░░░░░░░░█████░░░░░░░░░██░░░░░░░░░░░░░░░░░░░░░░████░░░
░░░██░░░░██░░░░░░░░░░░███░░░░░░░░░██░░░░░░░░░░░░░░░░░░░░███░░░░░░
░░░██░░░░██░█████░░░░░███░░░░░░█████░░░░███░░░░░█████░████████░░░
░░░██░██░██░░░░░░██░░░███░░░░██░░░██░░██░░███░░██░░░░░░░██░░░░░░░
░░░██░█████░░██████░░░███░░░░██░░░██░██░░░░██░░██░░░░░░░██░░░░░░░
░░░░███████░██░░░██░░░███░░░░██░░░██░██░░░░██░░██░░░░░░░██░░░░░░░
░░░░██░░░██░███████░░░░█████░░██████░░██████░░░██░░░░░░░██░░░░░░░
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
''')
    __cfg = parse_args()
    master = WaldorfMaster(__cfg)
    master.loop()
