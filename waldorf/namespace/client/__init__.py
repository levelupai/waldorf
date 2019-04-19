from socketio import ClientNamespace

from waldorf.client import _WaldorfSio
from waldorf.common import *

__all__ = ['Namespace']


class Namespace(ClientNamespace):
    """Client namespace."""

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
        # S2: Setup namespace.
        self.up = up
        self.ignore_events = []

    def set_response(self, api, resp: Response):
        self.up.logger.debug(
            'Get response from {}'.format(resp.hostname))
        key = api + '_resp'
        self.up.pvte_info.responses[key].append(resp)
        key = api + '_count'
        self.up.pvte_info.events[key] -= 1
        if self.up.pvte_info.events[key] <= 0:
            self.up.pvte_info.events[api].set()

    def on_connect(self):
        # S2.2: Require for master information.
        self.emit(WaldorfAPI.GET_INFO)

    def on_reconnect(self):
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
        # S2.3: Receive master information and put it into no-blocking queue.
        info = Response.decode(resp).resp
        assert isinstance(info, MasterPublInfo)
        self.up.remote_info = info
        self.up.nb_queue.put((WaldorfAPI.GET_INFO, info))

    def on_echo_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.ECHO, resp)

    def on_check_git_c_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.CHECK_GIT_C, resp)

    def on_get_env_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.GET_ENV, resp)

    def on_reg_task_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.REG_TASK, resp)

    def on_freeze_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.FREEZE, resp)

    def on_set_limit_resp(self, resp):
        resp = Response.decode(resp)
        limit = resp.resp
        self.up.logger.debug('Set limit to {}'.format(limit))
        self.up.pvte_info.set_limit(limit)
        self.up.nb_queue.put((WaldorfAPI.SET_LIMIT, 0))

    def on_gen_git_c_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.GEN_GIT_C, resp)

    def on_exit_resp(self, resp):
        resp = Response.decode(resp)
        self.set_response(WaldorfAPI.EXIT, resp)
