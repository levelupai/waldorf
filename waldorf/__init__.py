__version__ = '0.5.11'


class _WaldorfAPI(object):
    """Event names used by Waldorf"""
    GET_ENV = 'get_env'
    GET_CORES = 'get_cores'
    REG_TASK = 'reg_task'
    FREEZE = 'freeze'
    SUBMIT = 'submit'
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
    UPDATE_TABLE = 'update_table'
    RESTART_TASK = 'restart_task'
