import logging

__all__ = ['get_path', 'get_timestamp', 'get_run_timestamp',
           'get_system_info', 'get_network_info',
           'obj_encode', 'obj_decode', 'round2',
           'get_frame', 'init_logger', 'DummyLogger', 'Dummytqdm',
           'ColoredFormatter', 'get_linenumber', 'get_func_name']


def get_path(name='log', abspath=None, relative_path=None, _file=None):
    """Create path if path don't exist

    Args:
        name: folder name
        abspath: absolute path to be prefix
        relative_path: relative path that can be convert into absolute path
        _file: use directory based on _file

    Returns: Path of the folder

    """
    import os
    if abspath:
        directory = os.path.abspath(os.path.join(abspath, name))
    elif relative_path:
        directory = os.path.abspath(os.path.join(
            os.path.abspath(relative_path), name))
    else:
        if _file:
            directory = os.path.abspath(
                os.path.join(os.path.dirname(_file), name))
        else:
            directory = os.path.abspath(
                os.path.join(os.path.dirname(__file__), name))
    if not os.path.exists(directory):
        os.makedirs(directory)
    return directory


# return current timestamp in human-readable format
def get_timestamp():
    import datetime
    return datetime.datetime.now().strftime('%Y%m%d_%H%M%S')


import platform
import multiprocessing
import subprocess
import psutil
import re
from collections import namedtuple
import datetime

SystemInfo = namedtuple('SystemInfo',
                        ['os', 'cpu_type', 'cpu_count', 'mem'])
NetworkInfo = namedtuple('NetworkInfo', ['ip', 'hostname'])
get_run_timestamp = lambda: datetime.datetime.now().strftime(
    "%Y%m%d_%H%M%S")


def get_system_info():
    # Get OS
    os = platform.system()
    # Get more detailed OS version if Linux
    if platform.system() == 'Linux':
        issue = subprocess.check_output(['cat', '/etc/issue'])
        version = re.sub(r'\\\S', '', issue.decode()).strip()
        os = version

    # Get CPU type
    cpu_type = platform.processor()
    # Get more detailed CPU type if Linux
    if platform.system() == 'Linux':
        info = subprocess.check_output(['cat', '/proc/cpuinfo']).decode()
        for line in info.split('\n'):
            if 'model name' in line:
                cpu_type = re.sub(r'.*model name.*:\s*', '', line, 1)
                cpu_type = cpu_type
                break

    # Get usable CPU core count
    cpu_count = multiprocessing.cpu_count()

    # Get total RAM
    _mem_gb = psutil.virtual_memory().total / 1024.0 ** 3
    # Read memory info directly from kernel if Linux
    if platform.system() == 'Linux':
        info = subprocess.check_output(['cat', '/proc/meminfo']).decode()
        for line in info.split('\n'):
            if 'MemTotal' in line:
                match = re.search(r'MemTotal:\s+?(\d+?)\s', line)
                if match:
                    _mem_gb = float(match.group(1)) / 1024.0 ** 2
                break
    mem = '%.1f GB' % _mem_gb

    return SystemInfo(os, cpu_type, cpu_count, mem)


def get_network_info():
    import socket
    lan_ip = get_local_ip()
    hostname = socket.gethostname()
    return NetworkInfo(lan_ip, hostname)


import base64
import pickle
import zlib


def obj_encode(obj):
    return base64.b64encode(zlib.compress(pickle.dumps(obj), 9)).decode()


def obj_decode(obj):
    return pickle.loads(zlib.decompress(base64.b64decode(obj)))


def get_local_ip():
    import socket
    lan_ip = \
        (([ip for ip in socket.gethostbyname_ex(
            socket.gethostname())[2]
           if not ip.startswith('127.')]
          or [[(s.connect(('8.8.8.8', 53)),
                s.getsockname()[0], s.close())
               for s in [
                   socket.socket(
                       socket.AF_INET, socket.SOCK_DGRAM)]][0][1]])
         + ['no IP found'])[0]
    return lan_ip


import math


def round2(x, d=0):
    p = 10 ** d
    return float(math.floor((x * p) + math.copysign(0.5, x))) / p


BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE = range(8)
RESET_SEQ = '\033[0m'
COLOR_SEQ = '\033[1;%dm'
BOLD_SEQ = '\033[1m'
COLORS = {
    'WARNING': YELLOW,
    'INFO': WHITE,
    'DEBUG': BLUE,
    'CRITICAL': YELLOW,
    'ERROR': RED,
    'RED': RED,
    'GREEN': GREEN,
    'YELLOW': YELLOW,
    'BLUE': BLUE,
    'MAGENTA': MAGENTA,
    'CYAN': CYAN,
    'WHITE': WHITE,
}


class ColoredFormatter(logging.Formatter):
    def __init__(self, msg):
        logging.Formatter.__init__(self, msg)

    def format(self, record):
        levelname = record.levelname
        if levelname in COLORS:
            levelname_color = COLOR_SEQ % (
                    30 + COLORS[levelname]) + levelname + RESET_SEQ
            record.levelname = levelname_color
        message = logging.Formatter.format(self, record)
        message = message.replace('$RESET', RESET_SEQ) \
            .replace('$BOLD', BOLD_SEQ)
        for k, v in COLORS.items():
            message = message.replace('$' + k, COLOR_SEQ % (v + 30)) \
                .replace('$BG' + k, COLOR_SEQ % (v + 40)) \
                .replace('$BG-' + k, COLOR_SEQ % (v + 40))
        return message + RESET_SEQ


def get_frame():
    import sys
    return sys._getframe(1)


def init_logger(name, path=None, level=(logging.INFO, logging.DEBUG),
                enable=(True, True)):
    """Initialize a logger with certain name

    Args:
        name (str): Logger name
        path (str): Optional, specify which folder path
            the log file will be stored, for example
            '/tmp/log'
        level (tuple): Optional, consist of two logging level.
            The first stands for logging level of console handler,
            and the second stands for logging level of file handler.
        enable (tuple): Optional, define whether each handler is enabled.
            The first enables console handler,
            and the second enables file handler.

    Returns:
        logging.Logger: logger instance
    """
    import logging.handlers
    import sys
    import types
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = 0
    if path:
        path += '/' + name + '.log'
    else:
        path = get_path('log') + '/' + name + '.log'

    if enable[0]:
        _cf = ['$GREEN[%(asctime)s]$RESET',
               '[%(name)s]',
               '$BLUE[%(filename)20s:'
               '%(funcName)15s:%(lineno)5d]$RESET',
               '[%(levelname)s]',
               ' $CYAN%(message)s$RESET']
        cformatter = ColoredFormatter('-'.join(_cf))
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level[0])
        ch.setFormatter(cformatter)
        logger.addHandler(ch)

    if enable[1]:
        _nf = ['[%(asctime)s]',
               '[%(name)s]',
               '[%(filename)20s:%(funcName)15s:%(lineno)5d]',
               '[%(levelname)s]',
               ' %(message)s']
        nformatter = logging.Formatter('-'.join(_nf))
        rf = logging.handlers.RotatingFileHandler(
            path, maxBytes=50 * 1024 * 1024, backupCount=5)
        rf.setLevel(level[1])
        rf.setFormatter(nformatter)
        logger.addHandler(rf)

    def findCaller(self, stack_info=False, frame=None):
        """
        Find the stack frame of the caller so that we can note the source
        file name, line number and function name.
        """
        from logging import currentframe, os, _srcfile, io, traceback
        if frame:
            f = frame
        else:
            f = currentframe()
            # On some versions of IronPython, currentframe() returns None if
            # IronPython isn't run with -X:Frames.
            if f is not None:
                f = f.f_back
        rv = "(unknown file)", 0, "(unknown function)", None
        while hasattr(f, "f_code"):
            co = f.f_code
            filename = os.path.normcase(co.co_filename)
            if filename == _srcfile:
                f = f.f_back
                continue
            sinfo = None
            if stack_info:
                sio = io.StringIO()
                sio.write('Stack (most recent call last):\n')
                traceback.print_stack(f, file=sio)
                sinfo = sio.getvalue()
                if sinfo[-1] == '\n':
                    sinfo = sinfo[:-1]
                sio.close()
            rv = (co.co_filename, f.f_lineno, co.co_name, sinfo)
            break
        return rv

    def _log(self, level, msg, args, exc_info=None, extra=None,
             stack_info=False, frame=None):
        """
        Low-level logging routine which creates a LogRecord and then calls
        all the handlers of this logger to handle the record.
        """
        from logging import sys, _srcfile
        sinfo = None
        if _srcfile:
            # IronPython doesn't track Python frames, so findCaller raises an
            # exception on some versions of IronPython. We trap it here so that
            # IronPython can use logging.
            try:
                fn, lno, func, sinfo = self.findCaller(stack_info, frame)
            except ValueError:  # pragma: no cover
                fn, lno, func = "(unknown file)", 0, "(unknown function)"
        else:  # pragma: no cover
            fn, lno, func = "(unknown file)", 0, "(unknown function)"
        if exc_info:
            if isinstance(exc_info, BaseException):
                exc_info = (type(exc_info), exc_info, exc_info.__traceback__)
            elif not isinstance(exc_info, tuple):
                exc_info = sys.exc_info()
        record = self.makeRecord(self.name, level, fn, lno, msg, args,
                                 exc_info, func, extra, sinfo)
        self.handle(record)

    func_type = types.MethodType
    logger.findCaller = func_type(findCaller, logger)
    logger._log = func_type(_log, logger)
    return logger


class DummyLogger(object):
    """Dummy logger, replace all method with pass"""

    def __init__(self):
        pass

    def setLevel(self, level):
        pass

    def debug(self, msg, *args, **kwargs):
        pass

    def info(self, msg, *args, **kwargs):
        pass

    def warning(self, msg, *args, **kwargs):
        pass

    def warn(self, msg, *args, **kwargs):
        pass

    def error(self, msg, *args, **kwargs):
        pass

    def exception(self, msg, *args, exc_info=True, **kwargs):
        pass

    def critical(self, msg, *args, **kwargs):
        pass

    fatal = critical

    def log(self, level, msg, *args, **kwargs):
        pass

    def findCaller(self, stack_info=False, frame=None):
        pass

    def makeRecord(self, name, level, fn, lno, msg, args, exc_info,
                   func=None, extra=None, sinfo=None):
        pass

    def _log(self, level, msg, args, exc_info=None, extra=None,
             stack_info=False, frame=None):
        pass

    def handle(self, record):
        pass

    def addHandler(self, hdlr):
        pass

    def removeHandler(self, hdlr):
        pass

    def hasHandlers(self):
        pass

    def callHandlers(self, record):
        pass

    def getEffectiveLevel(self):
        pass

    def isEnabledFor(self, level):
        pass

    def getChild(self, suffix):
        pass


# a null device equivalent
class Dummytqdm(object):
    def update(self):
        pass

    def close(self):
        pass

    def unpause(self):
        pass

    def set_description(self, desc=None, refresh=True):
        pass

    def set_description_str(self, desc=None, refresh=True):
        pass

    def set_postfix(self, ordered_dict=None, refresh=True, **kwargs):
        pass

    def set_postfix_str(self, s='', refresh=True):
        pass

    def moveto(self, n):
        pass

    def clear(self, nolock=False):
        pass

    def refresh(self, nolock=False):
        pass


from inspect import currentframe


def get_linenumber():
    cf = currentframe()
    return cf.f_back.f_lineno


def get_func_name():
    import sys
    return sys._getframe(1).f_code.co_name
