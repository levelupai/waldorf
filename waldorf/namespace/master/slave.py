import socketio
import asyncio
import time
import uuid

from waldorf.master import _WaldorfWebApp
from waldorf.common import *
from waldorf.util import *

__all__ = ['SlaveNamespace']


class SlaveNamespace(socketio.AsyncNamespace):
    """Waldorf slave namespace."""

    async def trigger_event(self, event, *args):
        self.log_event(event, 'enter')
        ret = await super(SlaveNamespace, self).trigger_event(
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
        self.up = up
        self.ignore_events = ['update_table_resp', 'assign_core_resp']
        self.lost_timeout = 120
        self.gi = GroupInfo()
        self.info_lock = asyncio.Lock()
        self.exit_list = []
        asyncio.ensure_future(self.update())

    async def on_connect(self, sid, environ):
        """Slave connect.

        Collect information from the cookie and update server table.
        """
        await self.emit(WaldorfAPI.GET_INFO, room=sid)

    async def on_get_info(self, sid):
        resp = Response('master', 0, self.up.publ_info)
        await self.emit(WaldorfAPI.GET_INFO + '_resp',
                        resp.encode(), room=sid)

    async def on_get_info_resp(self, sid, resp):
        resp = Response.decode(resp)
        info = resp.resp
        assert isinstance(info, SlavePublInfo)
        self.up.logger.debug('info_lock acquire, {}.'.format(get_func_name()))
        await self.info_lock.acquire()
        # Check if uid exists
        if info.uid not in self.gi.uid_info:
            self.up.logger.debug(
                'Slave connect, hostname: {}, uid: {}'
                    .format(info.hostname, info.uid))
        else:
            self.up.logger.debug(
                'Slave reconnect, hostname: {}, uid: {}'
                    .format(info.hostname, info.uid))
        # Check version
        if info.version != self.up.publ_info.version:
            self.up.logger.debug(
                'Version mismatch. Local version: {}. '
                'Client version: {}.'.format(
                    self.up.publ_info.version, info.version))
            return

        self.gi.sid_uid[sid] = info.uid
        info.sid = sid
        info.record_conn_time()
        info.state = MachineState.INIT
        self.gi.uid_info[info.uid] = info
        self.enter_room(sid, 'slave')

        table_dict = info.to_table_dict()
        # Use _s to denote slave
        self.up.pvte_info.slv_tbl.update_object(
            info.uid + '_s', table_dict)

        self.up.pvte_info.ci.add_slave(info)
        self.up.logger.debug('Restart task.')
        await self.restart_task(sid)
        info.state = MachineState.ONLINE
        self.up.logger.debug('Assign core.')
        await self.assign_core()

        self.up.publ_info.slave_num += 1
        self.up.publ_info.cores = len(self.up.pvte_info.ci.cores)
        self.up.logger.debug('info_lock release, {}.'.format(get_func_name()))
        self.info_lock.release()

        resp = Response('master', 0, WaldorfAPI.GET_INFO, sid)
        await self.emit(WaldorfAPI.SYNC,
                        resp.encode(), room=resp.clt_sid)

    async def restart_task(self, slv_sid):
        for ri in self.up.pvte_info.ri.values():
            assert isinstance(ri, MasterRegisterInfo)
            key = slv_sid + '_' + WaldorfAPI.RESTART_TASK
            self.gi.events[key] = asyncio.Event()
            await self.up.pvte_info.clt_ns.get_env(
                ri.get_env_resp, restart=True, room=slv_sid)
            await self.gi.events[key].wait()
            for resp in ri.reg_task_resp:
                await self.up.pvte_info.clt_ns.reg_task(
                    resp, restart=True, room=slv_sid)
            await self.up.pvte_info.clt_ns.freeze(
                ri.freeze_resp, restart=True, room=slv_sid)

    async def on_update_table_resp(self, sid, resp):
        """API for updating table

        Update value of certain column
        args: k: column name, v: new column value
        """
        resp = Response.decode(resp)
        _info = resp.resp
        assert isinstance(_info, SlavePublInfo)

        info = self.gi.uid_info[_info.uid]
        info.ready = _info.ready
        info.used_cores = _info.used_cores
        info.used_cores = _info.used_cores
        info.load = _info.load
        info.load_str = _info.load_str

        table_dict = info.to_table_dict()
        # Use _s to denote slave
        self.up.pvte_info.slv_tbl.update_object(
            info.uid + '_s', table_dict)

    async def clean_up(self, uid):
        """Send clean up message to slave."""
        self.up.logger.debug('enter {}'.format(get_func_name()))
        self.gi.uid_info.pop(uid)
        self.up.logger.debug('leave {}'.format(get_func_name()))

    async def handle_disconnect(self, sid):
        self.up.logger.debug('info_lock acquire, {}.'.format(get_func_name()))
        await self.info_lock.acquire()
        uid = self.gi.sid_uid[sid]
        info = self.gi.uid_info[uid]
        assert isinstance(info, SlavePublInfo)
        info.record_disconn_time()
        if uid in self.exit_list:
            self.up.logger.debug(
                'Slave {} disconnect, uid: {}.'.format(
                    info.hostname, uid))
            info.state = MachineState.OFFLINE
            await self.clean_up(uid)
            self.exit_list.remove(uid)
        else:
            self.up.logger.debug(
                'Slave {} disconnect abnormally, uid: {}.'
                    .format(info.hostname, uid))
            info.state = MachineState.OFFLINE_AB

        self.leave_room(sid, 'slave')
        self.gi.sid_uid.pop(sid, None)

        # Update table
        table_dict = info.to_table_dict()
        self.up.pvte_info.slv_tbl.update_object(
            uid + '_s', table_dict)

        self.up.pvte_info.ci.remove_slave(info)
        await self.assign_core()

        self.up.publ_info.slave_num -= 1
        self.up.publ_info.cores = len(self.up.pvte_info.ci.cores)
        self.up.logger.debug('info_lock release, {}.'.format(get_func_name()))
        self.info_lock.release()

    async def on_disconnect(self, sid):
        """Slave disconnect.

        Update table and remove active connections.
        It will not remove the slave from the table.
        it will only set the disconnect time to current time
        and the state to offline. So the user will know
        when the slave disconnected when they checkout the master index page.
        """
        if sid not in self.gi.sid_uid:
            return
        asyncio.ensure_future(self.handle_disconnect(sid))

    async def on_exit(self, sid, resp):
        resp = Response.decode(resp)
        self.up.logger.debug(
            'Slave {} exit safely, uid: {}'.format(
                resp.hostname, resp.resp))
        self.exit_list.append(resp.resp)
        await self.handle_disconnect(sid)
        await self.emit(WaldorfAPI.EXIT + '_resp',
                        resp.encode(), room=sid)

    async def broadcast(self, api, resp: Response, room='slave'):
        if room != 'slave':
            await self.emit(api, resp.encode(), room=room)
            return
        self.up.logger.debug('info_lock acquire, {}.'.format(get_func_name()))
        await self.info_lock.acquire()
        slaves = [info.hostname
                  for info in self.gi.uid_info.values()]
        self.gi.events[resp.clt_sid + '_' + api + '_all'] = slaves
        await self.emit(api, resp.encode(), room='slave')
        self.up.logger.debug('info_lock release, {}.'.format(get_func_name()))
        self.info_lock.release()

    def get_resp(self, api, resp: Response):
        hostname = resp.hostname
        self.up.logger.debug('Get {} response from {}'.format(
            api, hostname))
        key = resp.clt_sid + '_' + api + '_all'
        self.gi.events[key].remove(hostname)
        self.up.logger.debug('Remaining {}'.format(
            self.gi.events[key]))
        if not self.gi.events[key]:
            self.gi.events.pop(key)

    async def echo(self, resp: Response):
        """Echo message, just for test."""
        await self.broadcast(WaldorfAPI.ECHO, resp)

    async def on_echo_resp(self, sid, resp):
        """Echo response from slaves."""
        resp = Response.decode(resp)
        clt_sid = resp.clt_sid

        self.get_resp(WaldorfAPI.ECHO, resp)
        if clt_sid not in self.up.pvte_info.clt_ns \
                .gi.sid_uid:
            self.up.logger.warning('sid not in client list.')
            return

        await self.up.pvte_info.clt_ns.echo_resp(resp)

    async def get_env(self, resp, room):
        await self.broadcast(WaldorfAPI.GET_ENV, resp, room)

    async def on_get_env_resp(self, sid, resp):
        """Get environment response from slaves.

        It will send a response to client when all slaves reply their responses.
        """
        resp = Response.decode(resp)
        clt_sid = resp.clt_sid
        _resp, restart = resp.resp

        if restart:
            key = sid + '_' + WaldorfAPI.RESTART_TASK
            self.gi.events[key].set()
            return

        self.get_resp(WaldorfAPI.GET_ENV, resp)
        if clt_sid not in self.up.pvte_info.clt_ns \
                .gi.sid_uid:
            self.up.logger.warning('sid not in client list.')
            return

        resp = Response(resp.hostname, _resp[0], _resp[1], clt_sid)
        await self.up.pvte_info.clt_ns.get_env_resp(resp)

    async def reg_task(self, resp, room):
        await self.broadcast(WaldorfAPI.REG_TASK, resp, room)

    async def on_reg_task_resp(self, sid, resp):
        resp = Response.decode(resp)
        clt_sid = resp.clt_sid
        _resp, restart = resp.resp

        if restart:
            return

        self.get_resp(WaldorfAPI.REG_TASK, resp)
        if clt_sid not in self.up.pvte_info.clt_ns \
                .gi.sid_uid:
            self.up.logger.warning('sid not in client list.')
            return

        resp = Response(resp.hostname, resp.code, _resp, clt_sid)
        await self.up.pvte_info.clt_ns.reg_task_resp(resp)

    async def freeze(self, resp, room):
        await self.broadcast(WaldorfAPI.FREEZE, resp, room)

    async def on_freeze_resp(self, sid, resp):
        """Freeze response from slaves."""
        resp = Response.decode(resp)
        clt_sid = resp.clt_sid
        _resp, restart = resp.resp

        if restart:
            return

        self.get_resp(WaldorfAPI.FREEZE, resp)
        if clt_sid not in self.up.pvte_info.clt_ns \
                .gi.sid_uid:
            self.up.logger.warning('sid not in client list.')
            return

    async def change_core(self, event, args):
        uid, core = args
        info = self.gi.uid_info[uid]
        assert isinstance(info, SlavePublInfo)
        if info.used_cores == core:
            return
        self.up.logger.info(
            'Change core, hostname: {} core: {} -> {}'.format(
                info.hostname, info.used_cores, core))
        _prev_used_cores = info.get_used_cores_uid()
        slv_sid = info.sid
        key = slv_sid + '_' + WaldorfAPI.CHANGE_CORE
        self.gi.events[key] = asyncio.Event()
        resp = Response('master', 0, core)
        await self.emit(WaldorfAPI.CHANGE_CORE,
                        resp.encode(), room=slv_sid)
        self.up.logger.debug('Wait for change core response...')
        await self.gi.events[key].wait()
        self.gi.events.pop(key)

        info.used_cores = core
        table_dict = info.to_table_dict()
        # Update table
        self.up.pvte_info.slv_tbl.update_object(
            uid + '_s', table_dict)
        # Update available cores
        _used_cores = info.get_used_cores_uid()
        if len(_prev_used_cores) < len(_used_cores):
            _cores = list(set(_used_cores) - set(_prev_used_cores))
            self.up.logger.debug('Add {} {}'.format(
                len(_cores), _cores))
            self.up.pvte_info.ci.update_cores(add=_cores)
        else:
            _cores = list(set(_prev_used_cores) - set(_used_cores))
            self.up.logger.debug('Sub {} {}'.format(
                len(_cores), _cores))
            self.up.pvte_info.ci.update_cores(sub=_cores)
        await self.assign_core()
        event.set()

    async def on_change_core_resp(self, sid, resp):
        """Change core response from slaves."""
        uid = self.gi.sid_uid[sid]
        key = uid + '_' + WaldorfAPI.CHANGE_CORE + '_resp'
        self.gi.events[key] = resp
        self.gi.events[sid + '_' + WaldorfAPI.CHANGE_CORE].set()

    async def assign_core(self):
        self.up.logger.debug('enter {}'.format(get_func_name()))
        _task_uid = str(uuid.uuid4())
        _event_key = _task_uid + '_' + WaldorfAPI.ASSIGN_CORE + '_event'
        _all_key = _task_uid + '_' + WaldorfAPI.ASSIGN_CORE + '_all'

        wait_flag = True
        while wait_flag:
            for _key in list(self.gi.events.keys()):
                if WaldorfAPI.ASSIGN_CORE in _key:
                    await asyncio.sleep(0.2)
                    continue
            wait_flag = False

        slaves = [info.hostname
                  for info in self.gi.uid_info.values()
                  if info.state == MachineState.ONLINE]
        if len(slaves) == 0:
            self.up.logger.debug('leave {}'.format(get_func_name()))
            return
        event = asyncio.Event()
        self.gi.events[_event_key] = event
        self.gi.events[_all_key] = slaves
        await self.up.pvte_info.clt_ns.set_limit()
        cores_info = self.up.pvte_info.ci
        for uid, info in self.gi.uid_info.items():
            if info.state != MachineState.ONLINE:
                continue
            # self.up.logger.debug(
            #     'Send request to {}'
            #         .format(info.hostname))
            resp = Response(
                'master', 0, cores_info.slv_core_clt[uid], _task_uid)
            await self.emit(WaldorfAPI.ASSIGN_CORE,
                            resp.encode(), room=info.sid)

        timeout = 20
        begin_time = time.time()
        while not event.is_set():
            await asyncio.sleep(0.1)
            if time.time() - begin_time > timeout:
                self.up.logger.warning(
                    'Timeout. Cannot receive response from [{}]'.format(
                        ','.join(self.gi.events[_all_key])))
                break
        self.gi.events.pop(_event_key)
        self.gi.events.pop(_all_key)
        self.up.logger.debug('leave {}'.format(get_func_name()))

    async def on_assign_core_resp(self, sid, resp):
        resp = Response.decode(resp)

        hostname = resp.hostname
        _task_uid = resp.clt_sid
        # self.up.logger.debug(
        #     'Get {} response from {}, task_uid {}'.format(
        #         WaldorfAPI.ASSIGN_CORE, hostname, _task_uid))
        _event_key = _task_uid + '_' + WaldorfAPI.ASSIGN_CORE + '_event'
        _all_key = _task_uid + '_' + WaldorfAPI.ASSIGN_CORE + '_all'
        self.gi.events[_all_key].remove(hostname)
        # self.up.logger.debug('Remaining {}'.format(
        #     self.gi.events[_all_key]))
        if len(self.gi.events[_all_key]) == 0:
            event = self.gi.events[_event_key]
            assert isinstance(event, asyncio.Event)
            event.set()

    async def clean_up_client(self, resp):
        await self.broadcast(WaldorfAPI.CLEAN_UP, resp)

    async def on_clean_up_resp(self, sid, resp):
        resp = Response.decode(resp)
        self.get_resp(WaldorfAPI.CLEAN_UP, resp)

    async def update(self):
        """Check connections every 5 seconds.

        If one slave disconnected over 120 seconds,
        the master server will automatically do cleaning up for the slave.
        """
        await asyncio.sleep(5)
        # Deal with disconnection
        await self.info_lock.acquire()
        now = time.time()
        keys = list(self.gi.uid_info.keys())
        for uid in keys:
            info = self.gi.uid_info[uid]
            assert isinstance(info, SlavePublInfo)
            if info.disconn_time != 0 and \
                    now - info.disconn_time > self.lost_timeout:
                self.up.logger.debug(
                    'Connection lost over {} sec. '
                    'Slave uid: {} is removed'.format(
                        self.lost_timeout, uid))
                await self.clean_up(uid)

        self.info_lock.release()
        # Update table
        await self.emit(WaldorfAPI.UPDATE_TABLE, room='slave')
        asyncio.ensure_future(self.update())
