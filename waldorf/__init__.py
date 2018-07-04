__version__ = '0.4.46'


class _WaldorfAPI(object):
    """Event names used by Waldorf"""
    GET_ENV = 'get_env'
    REG_TASK = 'reg_task'
    FREEZE = 'freeze'
    SUBMIT = 'submit'
    MAP = 'map'
    CLEAN_UP = 'clean_up'
    CHECK_SLAVE = 'check_slave'
    ECHO = 'echo'
    VER_MISMATCH = 'ver_mismatch'
    CHECK_VER = 'check_ver'
    GEN_GIT_C = 'gen_git_c'
    GET_INFO = 'get_info'
    CHANGE_CORE = 'change_core'
    UP_TIME = 'up_time'
    EXIT = 'exit'
