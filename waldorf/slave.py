from collections import deque
import multiprocessing as mp
from pathlib import Path
from typing import List
import threading
import argparse
import psutil
import time

from socketio import Client

from waldorf.cfg import WaldorfCfg, load_cfg, save_cfg
from waldorf.common import *
from waldorf.util import *


class SockWaitThread(threading.Thread):
    """Handle sock wait in an individual thread"""

    def __init__(self, up):
        super(SockWaitThread, self).__init__()
        self.up = up
        self.daemon = True

    def run(self):
        assert isinstance(self.up, _WaldorfSio)
        self.up.pvte_info.sio.wait()


class CheckCPUThread(threading.Thread):
    """Monitor CPU usage and change load average."""

    def __init__(self, up):
        super(CheckCPUThread, self).__init__()
        self.up = up
        self.cpu_count = mp.cpu_count()
        self.per_deque = deque(maxlen=15)
        self.total_deque = deque(maxlen=15)
        self.daemon = True

    def run(self):
        assert isinstance(self.up, _WaldorfSio)
        while self.up.alive:
            core = self.up.publ_info.used_cores
            affinity = [i for i in range(self.cpu_count)][-core:]
            # Calculate prefetch argument.
            average = 0
            total = 0
            for _ in range(10):
                per = psutil.cpu_percent(interval=1, percpu=True)
                if core != 0:
                    aver = sum([per[i] for i in affinity]) / core
                    average += aver
                total += sum(per) / self.cpu_count
            average /= 10
            total /= 10
            self.per_deque.append(average)
            self.total_deque.append(total)
            self.up.publ_info.load['per'] = average
            self.up.publ_info.load['total'] = total
            self.up.publ_info.load['per_5'] = \
                sum(self.per_deque) / len(self.per_deque)
            self.up.publ_info.load['total_5'] = \
                sum(self.total_deque) / len(self.total_deque)


class _WaldorfSio(mp.Process):
    def __init__(self, sio_queue: List[mp.Queue],
                 nb_queue: mp.Queue):
        super(_WaldorfSio, self).__init__()
        self.sio_queue = sio_queue
        self.nb_queue = nb_queue
        self.alive = True
        self.publ_info = SlavePublInfo()
        self.pvte_info = SlavePvteInfo()
        self.remote_info = MasterPublInfo()

    def setup(self):
        self.publ_info = self.sio_queue[0].get()
        self.cfg = self.sio_queue[0].get()

        assert isinstance(self.publ_info, SlavePublInfo)
        self.publ_info.cfg = self.cfg
        self.pvte_info.cfg = self.cfg

        self.setup_logger()

        self.publ_info.update_load()

        # S1.1: Setup namespace.
        from waldorf.namespace.slave import Namespace
        self.logger.debug(
            'Connect to {}:{} with uid {}'.format(
                self.cfg.master_ip, self.cfg.waldorf_port,
                self.publ_info.uid))
        if self.cfg.debug >= 2:
            sio = Client(logger=self.logger)
        else:
            sio = Client()
        self.pvte_info.sio = sio
        ns = Namespace('/slave')
        ns.setup(self)
        self.pvte_info.ns = ns
        sio.register_namespace(ns)
        sio.connect('http://{}:{}'.format(
            self.cfg.master_ip, self.cfg.waldorf_port))

    def setup_logger(self):
        log_path = get_path(
            'log', abspath=str(Path.home()) + '/.waldorf')
        if self.cfg.debug >= 1:
            import logging
            self.logger = init_logger(
                'wd_slave', log_path, (logging.DEBUG, logging.DEBUG))
        else:
            import logging
            self.logger = init_logger(
                'wd_slave', log_path, (logging.INFO, logging.DEBUG))

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

    def on_exit(self):
        resp = Response(self.publ_info.hostname,
                        0, self.publ_info.uid)
        self.get_response(WaldorfAPI.EXIT, resp, count=0)
        time.sleep(0.05)

    def run(self):
        self.setup()
        CheckCPUThread(self).start()
        SockWaitThread(self).start()

        while True:
            cmd = self.sio_queue[0].get()
            if cmd[0] == 'exit':
                self.logger.info('Receive exit command.')
                self.on_exit()
                self.logger.debug('Terminate workers.')
                self.pvte_info.ns.terminate_workers(
                    self.publ_info.cores_uid)
                self.alive = False
                break
        self.logger.debug('Disconnect.')
        self.pvte_info.sio.disconnect()
        self.logger.debug('Send signal to main process.')
        self.sio_queue[1].put(0)
        self.logger.debug('End')


class SioResultThread(threading.Thread):
    """Handling result from sio using a queue."""

    def __init__(self, up, nb_queue, events):
        super(SioResultThread, self).__init__()
        self.up = up
        self.nb_queue = nb_queue
        self.events = events
        self.daemon = True

    def run(self):
        assert isinstance(self.up, WaldorfSlave)
        while self.up.running:
            resp, result = self.nb_queue.get()
            if resp == WaldorfAPI.CHANGE_CORE:
                cfg = result
                self.up.cfg = cfg
            if resp == WaldorfAPI.SYNC:
                key = WaldorfAPI.SYNC
                self.events[key].set()


class WaldorfSlave(object):
    def __init__(self, cfg: WaldorfCfg):
        self.cfg = cfg
        self.debug = cfg.debug
        self.running = True

        # S0.2: Collect information.
        self.publ_info = SlavePublInfo()
        self.publ_info.used_cores = self.cfg.core

        # S0.3: Setup information queues.
        self._sio_queue = [mp.Queue(), mp.Queue()]
        self._sio_noblock_queue = mp.Queue()

        # S0.4: Use no-blocking queue to collect result from socketio process.
        self._events = {}
        SioResultThread(self, self._sio_noblock_queue,
                        self._events).start()

        # S0.5: Setup socketio client.
        key = WaldorfAPI.SYNC
        self._events[key] = threading.Event()
        self._sio_queue[0].put(self.publ_info)
        self._sio_queue[0].put(self.cfg)
        self._sio_p = _WaldorfSio(self._sio_queue,
                                  self._sio_noblock_queue)
        self._sio_p.start()

        self.remote_info = self._get()[1]
        key = WaldorfAPI.SYNC
        self._events[key].wait()
        if self.remote_info.version != self.publ_info.version:
            self._put('exit')
            self._get()
            self._sio_p.terminate()
            raise Exception(
                'Version mismatch. Local version: {}. '
                'Master version: {}.'.format(
                    self.publ_info.version,
                    self.remote_info.version)
            )

    def _put(self, cmd: str, args=None):
        self._sio_queue[0].put((cmd, args))

    def _get(self):
        return self._sio_queue[1].get()

    def loop(self):
        try:
            while True:
                cmd = input('cmd:\n')
                if cmd == 'exit':
                    print('L{}: Exiting'.format(get_linenumber()))
                    self._put(cmd)
                    self._get()
                    break
        except KeyboardInterrupt:
            self._put('exit')
            self._get()
        save_cfg('slave', self.cfg)
        print('L{}: End'.format(get_linenumber()))


def parse_args():
    # S0.1: Load configuration.
    _cfg = load_cfg('slave')
    parser = argparse.ArgumentParser(description='Waldorf slave')
    parser.add_argument('-i', '--ip', type=str, default=_cfg.master_ip)
    parser.add_argument('-p', '--port', type=int, default=_cfg.waldorf_port)
    parser.add_argument('-c', '--core', type=int, default=_cfg.core)
    parser.add_argument('--broker_ip', default=None)
    parser.add_argument('--backend_ip', default=None)
    parser.add_argument('--redis_port', type=int, default=_cfg.redis_port)
    parser.add_argument('-d', '--debug', type=int, default=_cfg.debug)
    args = parser.parse_args()
    _cfg.set_ip(args.ip, args.broker_ip, args.backend_ip)
    _cfg.waldorf_port = args.port
    _cfg.core = min(args.core, mp.cpu_count())
    _cfg.redis_port = args.redis_port
    _cfg.debug = args.debug
    _cfg.update()
    save_cfg('slave', _cfg)
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
    mp.set_start_method('spawn')
    __cfg = parse_args()
    slave = WaldorfSlave(__cfg)
    slave.loop()
