import socketio
import asyncio
import pickle
import redis
import time

from waldorf.master import _WaldorfWebApp
from waldorf.common import *
from waldorf.util import *

__all__ = ['ClientNamespace']


class ClientNamespace(socketio.AsyncNamespace):
    """Waldorf client namespace."""

    async def trigger_event(self, event, *args):
        self.log_event(event, 'enter')
        ret = await super(ClientNamespace, self).trigger_event(
            event, *args)
        self.log_event(event, 'leave')
        return ret

    def log_event(self, event, msg):
        if event in self.ignore_events:
            return
        self.up.logger.debug('{} {}'.format(msg, event))

    def setup(self, up: _WaldorfWebApp):
        """Setup namespace.

        Set up as parent process,
        using up to transfer information between namespace.
        """
        # CS0: Setup client namespace.
        self.up = up
        self.ignore_events = []
        self.lost_timeout = 30
        self.gi = GroupInfo()
        self.info_lock = asyncio.Lock()
        self.exit_list = []
        self.app_name_dict = {}

        # For clean up app queue
        self._redis_client = redis.StrictRedis(
            host=self.up.cfg.broker_ip, port=self.up.cfg.redis_port)
        asyncio.ensure_future(self.update())

    async def on_connect(self, sid, environ):
        """Client connect.

        Collect information from the cookie and update server table.
        """
        # CS1: Require client information.
        await self.emit(WaldorfAPI.GET_INFO, room=sid)

    async def on_get_info(self, sid):
        resp = Response('master', 0, self.up.publ_info)
        await self.emit(WaldorfAPI.GET_INFO + '_resp',
                        resp.encode(), room=sid)

    async def on_get_info_resp(self, sid, resp):
        # CS2: Receive client information and check version.
        resp = Response.decode(resp)
        info = resp.resp
        assert isinstance(info, ClientPublInfo)
        self.up.logger.debug('info_lock acquire, {}.'.format(get_func_name()))
        await self.info_lock.acquire()
        if info.uid not in self.gi.uid_info:
            self.up.logger.debug(
                'Client connect, hostname: {}, uid: {}'
                    .format(info.hostname, info.uid))
        else:
            self.up.logger.debug(
                'Client reconnect, hostname: {}, uid: {}'
                    .format(info.hostname, info.uid))
        if info.version != self.up.publ_info.version:
            self.up.logger.debug(
                'Version mismatch. Local version: {}. '
                'Client version: {}.'.format(
                    self.up.publ_info.version, info.version))
            return

        self.up.publ_info.client_num += 1
        self.gi.sid_uid[sid] = info.uid
        info.sid = sid
        info.record_conn_time()
        self.gi.uid_info[info.uid] = info
        self.enter_room(sid, 'client')

        table_dict = info.to_table_dict()
        # Use _c to denote client
        self.up.pvte_info.clt_tbl.update_object(
            info.uid + '_c', table_dict)

        self.up.logger.debug('info_lock release, {}.'.format(get_func_name()))
        self.info_lock.release()

        resp = Response('master', 0, WaldorfAPI.GET_INFO, sid)
        await self.emit(WaldorfAPI.SYNC,
                        resp.encode(), room=resp.clt_sid)

    async def echo_resp(self, resp: Response):
        await self.emit(WaldorfAPI.ECHO + '_resp',
                        resp.encode(), room=resp.clt_sid)

    async def on_echo(self, sid):
        """Echo message, just for test."""
        resp = Response('master', 0, 0, sid)
        await self.echo_resp(resp)
        await self.up.pvte_info.slv_ns.echo(resp)

    async def on_check_git_c(self, sid, resp):
        resp = Response.decode(resp)
        cfg = resp.resp
        try:
            decrypted = self.up.decrypt(
                cfg.env_cfg.git_credential)
            cfg.env_cfg.git_credential = pickle.loads(decrypted)
            self.up.logger.info('Credential verified.')
        except:
            resp = Response(
                'master', -1,
                'Error while parsing git credential.', sid)
            await self.emit(WaldorfAPI.CHECK_GIT_C + '_resp',
                            resp.encode(), room=resp.clt_sid)
            return

        resp = Response(
            'master', 0,
            'Parse git credential success.', sid)
        await self.emit(WaldorfAPI.CHECK_GIT_C + '_resp',
                        resp.encode(), room=resp.clt_sid)

    async def get_env_resp(self, resp: Response):
        await self.emit(WaldorfAPI.GET_ENV + '_resp',
                        resp.encode(), room=resp.clt_sid)

    async def get_env(self, resp, restart=False, room='slave'):
        sid = resp.clt_sid
        uid = self.gi.sid_uid[sid]
        name, pairs, suites, cfg = resp.resp
        args = uid, name, pairs, suites, cfg, restart
        resp = Response('master', 0, args, sid)
        await self.up.pvte_info.slv_ns.get_env(resp, room)

        if restart:
            return

        resp = Response('master', 0, '', sid)
        await self.get_env_resp(resp)

    async def on_get_env(self, sid, resp):
        """Receive client's get env request.

        Setup environment on slave. Git credential will be decoded on master.
        """
        resp = Response.decode(resp)
        key = sid + '_' + WaldorfAPI.GET_ENV
        if key not in self.gi.events:
            self.gi.events[key] = {'count': 0}
        if resp.resp == 'Finished':
            args = list(self.gi.events[key][0])
            cfg = args[3]
            if cfg.env_cfg.git_credential is not None:
                decrypted = self.up.decrypt(
                    cfg.env_cfg.git_credential)
                cfg.env_cfg.git_credential = pickle.loads(decrypted)
            args[2] = []
            args = tuple(args)
            count = self.gi.events[key]['count']
            for i in range(count - 1):
                args[2].append(self.gi.events[key][i + 1])
            self.gi.events.pop(key)
            resp.resp = args
            uid = self.gi.sid_uid[sid]
            resp.clt_sid = sid
            self.up.pvte_info.update_reg_info(
                uid, WaldorfAPI.GET_ENV, resp)

            await self.get_env(resp)
        else:
            count = self.gi.events[key]['count']
            self.gi.events[key][count] = resp.resp
            self.gi.events[key]['count'] += 1
            resp = Response('master', 0, '', sid)
            await self.get_env_resp(resp)

    async def reg_task_resp(self, resp: Response):
        await self.emit(WaldorfAPI.REG_TASK + '_resp',
                        resp.encode(), room=resp.clt_sid)

    async def reg_task(self, resp, restart=False, room='slave'):
        sid = resp.clt_sid
        uid = self.gi.sid_uid[sid]
        task_name, task_code, opts = resp.resp
        args = uid, task_name, task_code, opts, restart
        resp = Response('master', 0, args, sid)
        await self.up.pvte_info.slv_ns.reg_task(resp, room)

        if restart:
            return

        resp = Response('master', 0, '', sid)
        await self.reg_task_resp(resp)

    async def on_reg_task(self, sid, resp):
        """Register task on slave.

        Send task information to slave.
        """
        resp = Response.decode(resp)

        uid = self.gi.sid_uid[sid]
        resp.clt_sid = sid
        self.up.pvte_info.update_reg_info(
            uid, WaldorfAPI.REG_TASK, resp)

        await self.reg_task(resp)

    async def freeze_resp(self, resp: Response):
        await self.emit(WaldorfAPI.FREEZE + '_resp',
                        resp.encode(), room=resp.clt_sid)

    async def freeze(self, resp, restart=False, room='slave'):
        sid = resp.clt_sid
        uid = self.gi.sid_uid[sid]
        hostname = resp.hostname
        prefetch_multi = resp.resp
        args = uid, hostname, prefetch_multi, restart
        resp = Response('master', 0, args, sid)
        await self.up.pvte_info.slv_ns.freeze(resp, room)

        await asyncio.sleep(1)
        if not restart:
            info = self.gi.uid_info[uid]
            self.up.pvte_info.ci.add_client(info)

        if restart:
            return

        await self.up.pvte_info.slv_ns.assign_core()
        resp = Response('master', 0, '', sid)
        await self.freeze_resp(resp)

    async def on_freeze(self, sid, resp):
        """Freeze slave tasks configuration."""
        resp = Response.decode(resp)
        uid = self.gi.sid_uid[sid]
        resp.clt_sid = sid
        self.up.pvte_info.update_reg_info(
            uid, WaldorfAPI.FREEZE, resp)
        self.up.pvte_info.update_reg_info(
            uid, 'hostname', resp.hostname)
        self.up.pvte_info.confirm_reg_info(uid)

        await self.freeze(resp)

    async def set_limit(self):
        for uid in self.up.pvte_info.ci.clt_list:
            info = self.gi.uid_info[uid]
            sid = self.gi.uid_info[uid].sid
            limit = self.up.pvte_info.ci.each[uid]
            self.up.logger.debug('Set the limit of {} to {}'.format(
                info.hostname, limit))
            resp = Response('master', 0, limit, sid)
            await self.emit(WaldorfAPI.SET_LIMIT + '_resp',
                            resp.encode(), room=sid)

    async def on_set_limit(self, sid, resp):
        resp = Response.decode(resp)
        limit = resp.resp
        uid = self.gi.sid_uid[sid]
        if uid not in self.up.pvte_info.ci.clt_list:
            resp = Response('master', 0, 1)
            await self.emit(WaldorfAPI.SET_LIMIT + '_resp',
                            resp.encode())
            return

        self.up.pvte_info.ci.set_clt_limit(uid, limit)
        if self.up.pvte_info.ci.changed:
            await self.up.pvte_info.slv_ns.assign_core()
        else:
            limit = self.up.pvte_info.ci.each[uid]
            resp = Response('master', 0, limit, sid)
            await self.emit(WaldorfAPI.SET_LIMIT + '_resp',
                            resp.encode(), room=sid)

    async def update(self):
        """Check connections every 5 seconds.

        If one client disconnected over 120 seconds,
        the master server will automatically do cleaning up
        for the client.
        """
        await asyncio.sleep(5)
        # Deal with disconnection.
        await self.info_lock.acquire()
        now = time.time()
        keys = list(self.gi.uid_info.keys())
        for uid in keys:
            info = self.gi.uid_info[uid]
            assert isinstance(info, ClientPublInfo)
            if info.disconn_time != 0 and \
                    now - info.disconn_time > self.lost_timeout:
                self.up.logger.debug(
                    'Connection lost over {} sec. '
                    'Client uid: {} is removed'.format(
                        self.lost_timeout, uid))
                await self.clean_up(uid)

        self.info_lock.release()
        self.clean_up_app_queue()
        asyncio.ensure_future(self.update())

    def clean_up_app_queue(self):
        # Clean celery app queue
        _clean_queue = []
        for k, v in self.app_name_dict.items():
            if time.time() - v >= 60:
                _clean_queue.append(k)
        for app_name in _clean_queue:
            self._redis_client.expire(app_name, 120)
            self.app_name_dict.pop(app_name)
            self.up.logger.debug(
                'Clean up app queue. '
                'app_name: {}'.format(app_name))

    async def clean_up(self, uid):
        """Send clean up message to slave."""
        self.up.logger.debug('enter {}'.format(get_func_name()))
        info = self.gi.uid_info.pop(uid)
        assert isinstance(info, ClientPublInfo)
        self.up.pvte_info.remove_reg_info(uid)
        clients = '\n'.join(
            ['{} {}'.format(info.hostname, uid)
             for uid, info in self.up.pvte_info.ri.items()])
        self.up.logger.debug(
            '\nRegistered client uid:\n{}'.format(clients))
        resp = Response('master', 0, uid, info.sid)
        await self.up.pvte_info.slv_ns.clean_up_client(resp)

        self.up.pvte_info.ci.remove_client(info)
        await self.up.pvte_info.slv_ns.assign_core()

        # Clean celery app queue
        app_name = 'app-' + uid
        self.app_name_dict[app_name] = time.time()
        self.up.logger.debug('leave {}'.format(get_func_name()))

    async def handle_disconnect(self, sid):
        if sid not in self.gi.sid_uid:
            return
        self.up.logger.debug('info_lock acquire, {}.'.format(get_func_name()))
        await self.info_lock.acquire()
        uid = self.gi.sid_uid[sid]
        self.up.logger.debug('Handle disconnect for sid: {}, uid: {}.'
                             .format(sid, uid))
        info = self.gi.uid_info[uid]
        assert isinstance(info, ClientPublInfo)
        info.record_disconn_time()
        if uid in self.exit_list:
            self.up.logger.debug(
                'Client {} disconnect, uid: {}.'.format(
                    info.hostname, uid))
            info.state = MachineState.OFFLINE
            await self.clean_up(uid)
            self.exit_list.remove(uid)
        else:
            self.up.logger.debug(
                'Client {} disconnect abnormally, uid: {}.'
                    .format(info.hostname, uid))
            info.state = MachineState.OFFLINE_AB

        self.leave_room(sid, 'client')
        self.gi.sid_uid.pop(sid, None)
        self.up.publ_info.client_num -= 1

        # Update table
        table_dict = info.to_table_dict()
        self.up.pvte_info.clt_tbl.update_object(
            uid + '_c', table_dict)
        self.up.logger.debug('info_lock release, {}.'.format(get_func_name()))
        self.info_lock.release()

    async def on_disconnect(self, sid):
        """Client disconnect.

        Update table and remove active connections.
        """
        if sid not in self.gi.sid_uid:
            return
        asyncio.ensure_future(self.handle_disconnect(sid))

    async def on_exit(self, sid, resp):
        resp = Response.decode(resp)
        self.up.logger.debug(
            'Client {} exit safely, uid: {}'.format(
                resp.hostname, resp.resp))
        self.exit_list.append(resp.resp)
        await self.handle_disconnect(sid)
        await self.emit(WaldorfAPI.EXIT + '_resp',
                        resp.encode(), room=sid)

    async def on_gen_git_c(self, sid):
        """Generate git credential.

        Send public key to client.
        """
        resp = Response('master', 0, self.up._public_pem)
        await self.emit(WaldorfAPI.GEN_GIT_C + '_resp',
                        resp.encode(), room=sid)
