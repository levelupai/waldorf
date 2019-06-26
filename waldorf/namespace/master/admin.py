import socketio
import asyncio
import json
import time

from waldorf.master import _WaldorfWebApp
from waldorf.common import *
from waldorf.util import *

__all__ = ['AdminNamespace']


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
        self.gi = GroupInfo()
        asyncio.ensure_future(self.update())

    def create_info(self):
        _c_table = self.up.pvte_info.clt_tbl.to_dict()
        _s_table = self.up.pvte_info.slv_tbl.to_dict()
        objects = {}
        objects.update(_c_table['objects'])
        objects.update(_s_table['objects'])
        resp = {
            'version': self.up.publ_info.version,
            'c_head': _c_table['head'],
            's_head': _s_table['head'],
            'objects': objects
        }
        return resp

    async def update(self):
        await asyncio.sleep(1)
        await self.emit(WaldorfAPI.GET_INFO + '_resp',
                        self.create_info(), room='admin')
        asyncio.ensure_future(self.update())

    async def on_connect(self, sid, environ):
        self.up.logger.debug('enter {}'.format(get_func_name()))
        self.enter_room(sid, 'admin')
        self.up.logger.debug('leave {}'.format(get_func_name()))

    async def on_disconnect(self, sid):
        self.leave_room(sid, 'admin')

    async def on_get_info(self, sid):
        """API for getting information.

        Get information of Waldorf slaves and clients.
        """
        await self.emit(WaldorfAPI.GET_INFO + '_resp',
                        self.create_info(), room=sid)

    async def on_change_core(self, sid, args):
        """API for changing used core.

        Change the used cores of the given uid.
        """
        uid, core = args
        if not isinstance(core, int):
            return
        self.up.logger.debug('enter {}'.format(get_func_name()))
        event = asyncio.Event()
        await self.up.pvte_info.slv_ns.change_core(event, args)
        await event.wait()
        info = json.dumps([0, 'Success.'])
        await self.emit(
            WaldorfAPI.CHANGE_CORE + '_resp', info, room=sid)
        self.up.publ_info.cores = len(self.up.pvte_info.ci.cores)
        self.up.logger.debug('Change core success.')
        self.up.logger.debug('leave {}'.format(get_func_name()))

    async def on_up_time(self, sid):
        """API for getting master up time."""
        await self.emit(
            WaldorfAPI.UP_TIME + '_resp',
            time.time() - self.up.publ_info.up_time, room=sid)
