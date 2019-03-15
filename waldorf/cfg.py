import multiprocessing as mp
import jsonpickle
from pathlib import Path
from waldorf.util import get_path


class WaldorfEnvCfg(object):
    """Configuration about loading virtual environment"""

    def __init__(self):
        self.already_exist = 'keep'
        self.version_mismatch = 'keep'
        self.default_expect = r'(?!\S+@\S+)(?:[:][^:]+?[$])'
        self.default_timeout = 60
        self.git_credential = None


class WaldorfCfg(object):
    """Configuration of Waldorf"""

    def __init__(self, master_ip=None, broker_ip=None, backend_ip=None,
                 broker='redis', backend='redis', debug=0):
        """Waldorf basic configuration

        Args:
            master_ip: waldorf master ip. If None is given,
                the master ip will be set to `127.0.0.1`.
            broker_ip: broker ip used by celery workers. If None is given,
                this ip will be set to the master ip.
            backend_ip: backend ip used by celery workers. If None is given,
                this ip will be set to the master ip.
            broker: broker type, support `rabbit` and `redis`,
                default is `redis`
            backend: backend type, support `redis` and `memcache`,
                the default is `redis`
            debug: whether you need debug output, 0 - no debug, 1 - verbose,
                2 - even more verbose
        """
        if master_ip:
            self.master_ip = master_ip
        else:
            self.master_ip = '127.0.0.1'
        if broker_ip:
            self.broker_ip = broker_ip
        else:
            self.broker_ip = self.master_ip
        if backend_ip:
            self.backend_ip = backend_ip
        else:
            self.backend_ip = self.master_ip
        self.broker = broker
        self.backend = backend
        self.debug = debug
        self.waldorf_port = 61801
        self.rabbit_user = 'waldorf'
        self.rabbit_pwd = 'waldorf'
        self.rabbit_port = 5672
        self.redis_port = 6379
        self.memcached_port = 11211
        self.core = mp.cpu_count()
        self.get_interval = 0.1
        self.submit_limit = 0
        self.result_timeout = 300
        # If retry is not enabled
        # it will return None when result is not available
        # It only work for timeout situation
        self.retry_enable = True
        self.retry_times = 3
        self.env_cfg = WaldorfEnvCfg()

    def set_ip(self, master_ip=None, broker_ip=None, backend_ip=None):
        """More convenient way to set ips.

        Args:
            master_ip: waldorf master ip. If None is given,
                the master ip will be set to `127.0.0.1`.
            broker_ip: broker ip used by celery workers. If None is given,
                this ip will be set to the master ip.
            backend_ip: backend ip used by celery workers. If None is given,
                this ip will be set to the master ip.
        """
        if master_ip:
            self.master_ip = master_ip
        else:
            self.master_ip = '127.0.0.1'
        if broker_ip:
            self.broker_ip = broker_ip
        else:
            self.broker_ip = self.master_ip
        if backend_ip:
            self.backend_ip = backend_ip
        else:
            self.backend_ip = self.master_ip

    def update(self):
        """Update setting.

        If ip is set after initialization,
        this function should be called to update Celery settings.
        """
        self.celery = {
            'broker': {
                'rabbit': 'amqp://{}:{}@{}:{}'.format(
                    self.rabbit_user, self.rabbit_pwd,
                    self.broker_ip, self.rabbit_port),
                'redis': 'redis://{}:{}'.format(self.broker_ip,
                                                self.redis_port)
            },
            'backend': {
                'redis': 'redis://{}:{}'.format(self.backend_ip,
                                                self.redis_port),
                'memcached': 'cache+memcached://{}:{}'.
                    format(self.backend_ip, self.memcached_port)
            }
        }
        self.celery_broker = self.celery['broker'][self.broker]
        self.celery_backend = self.celery['backend'][self.backend]

    @staticmethod
    def dumps(obj):
        return jsonpickle.encode(obj)

    @staticmethod
    def loads(string):
        return jsonpickle.decode(string)


def load_cfg(prefix):
    cfg_dir = get_path('cfg', abspath=str(Path.home()) + '/.waldorf')
    cfg_fp = cfg_dir + '/{}_cfg.json'.format(prefix)
    cfg_file = Path(cfg_fp)
    if cfg_file.exists() and cfg_file.is_file():
        return WaldorfCfg.loads(open(cfg_fp).read())
    return WaldorfCfg()


def save_cfg(prefix, cfg):
    cfg_dir = get_path('cfg', abspath=str(Path.home()) + '/.waldorf')
    cfg_fp = cfg_dir + '/{}_cfg.json'.format(prefix)
    with open(cfg_fp, 'w') as f:
        f.write(WaldorfCfg.dumps(cfg))
