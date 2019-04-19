from pathlib import Path
from waldorf.util import get_path
import os
import pexpect
import sys
import re
import traceback
from waldorf.cfg import WaldorfCfg


class EnvCmd(object):
    pass


class MajorCmd(EnvCmd):
    """Major commands.

    Major commands don't have to create session first.
    And it will overwrite current session.
    It will close session when it is done.
    The following commands are only used for demonstrating arguments.
    """
    CREATE_ENV = 'create_env'
    REMOVE_ENV = 'remove_env'
    CHECK_PY_VER = 'check_py_ver'

    @staticmethod
    def create_env(path=None):
        return 'create_env'

    @staticmethod
    def remove_env():
        return 'remove_env'

    @staticmethod
    def check_py_ver():
        return 'check_py_ver'


class _MajorCmd(EnvCmd):
    """Define major commands API"""

    def create_env(self, path=None):
        pass

    def remove_env(self):
        pass

    def check_py_ver(self):
        pass


class MinorCmd(EnvCmd):
    """Minor commands.

    Minor commands always need to create session first.
    And close session at the end of suite.
    The following commands are only used for demonstrating arguments.
    """
    CREATE_SESS = 'create_sess'
    CLOSE_SESS = 'close_sess'
    SOURCE_ENV = 'source_env'
    UPDATE_ENV_LIST = 'update_env_list'
    GIT_CLONE = 'git_clone'
    GIT_CMD = 'git_cmd'
    RUN_CMD = 'run_cmd'

    @staticmethod
    def create_sess():
        return 'create_sess'

    @staticmethod
    def close_sess():
        return 'close_sess'

    @staticmethod
    def source_env():
        return 'source_env'

    @staticmethod
    def update_env_list():
        return 'update_env_list'

    @staticmethod
    def git_clone(cmd, repo):
        return 'git_clone'

    @staticmethod
    def git_cmd(cmd, repo):
        return 'git_cmd'

    @staticmethod
    def run_cmd(cmd, expect=None):
        return 'run_cmd'


class _MinorCmd(EnvCmd):
    """Define minor commands API."""

    def create_sess(self):
        pass

    def close_sess(self):
        pass

    def source_env(self):
        pass

    def update_env_list(self):
        pass

    def git_clone(self, cmd, repo):
        pass

    def git_cmd(self, cmd, repo):
        pass

    def run_cmd(self, cmd, expect=None):
        pass


class CmdPair(object):
    """Command pair."""

    def __init__(self, cmd, args=None, kwargs=None, pattern=None, exist=True):
        """Initialize command pair.

        Args:
            cmd: command name from major commands or minor commands
            args: command arguments
            kwargs: keyword arguments
            pattern: regex pattern used by check suite
            exist: if the pattern exists, this determines whether
                check will return True or False
        """
        self.cmd = cmd
        if args:
            self.args = args
        else:
            self.args = []
        if kwargs:
            self.kwargs = kwargs
        else:
            self.kwargs = {}
        self.pattern = pattern
        self.exist = exist


class Suite(object):
    """A composite of command pairs.

    This contains three lists of command pairs.
    The setup commands and cleanup commands will be done
    at the beginning and the end, kind of like unittest.
    The cmd part run the actual installation or test.
    """

    def __init__(self, setup, cmd, cleanup):
        self.setup = setup
        self.cmd = cmd
        self.cleanup = cleanup


class SetupSuite(object):
    """Setup suite.

    This is consist of two suites.
    The check suite will run the check and see if the condition is matched.
    The setup suite will do some setup commands.
    """

    def __init__(self, check, setup):
        self.check = check
        self.setup = setup


class PExpect(object):
    def __init__(self, logger,
                 spawn='bash',
                 default_expect=r'(?!\S+@\S+)(?:[:][^:]+?[$])',
                 default_timeout=60
                 ):
        self.logger = logger
        self.spawn = spawn
        self.default_expect = default_expect
        self.timeout = default_timeout
        self.sess = pexpect.spawn(spawn, use_poll=True)
        self.sess.delayafterclose = 1
        self.sess.delayafterterminate = 1
        self.sess.ptyproc.delayafterclose = 1
        self.sess.ptyproc.delayafterterminate = 1
        self.sess.logfile_read = sys.stdout.buffer
        self.expect(self.default_expect)
        self.sess.setwinsize(200, 480)
        self.run('export LANGUAGE=en_US.UTF-8')
        self.expect(self.default_expect)

    def expect(self, expect):
        try:
            return self.sess.expect(expect, timeout=self.timeout)
        except pexpect.TIMEOUT:
            self.logger.error('Timeout. Check out your expect string'
                             ' or change default timeout.')
            return -1
        except Exception as e:
            self.logger.error(e)
            return -1

    def run(self, cmd, expect=None):
        self.logger.info('CMD: {}'.format(cmd))
        self.sess.sendline(cmd)
        if expect:
            self.expect(expect)
        else:
            self.expect(self.default_expect)
        return self.sess.before

    def close(self):
        print()
        self.sess.close()


class WaldorfEnv(_MajorCmd, _MinorCmd):
    def __init__(self, name, cfg: WaldorfCfg, logger):
        self.name = name
        self.cfg = cfg
        self.logger = logger
        self._tmp_sess = None
        self.git_credential = self.cfg.env_cfg.git_credential
        self.default_expect = self.cfg.env_cfg.default_expect
        self.default_timeout = self.cfg.env_cfg.default_timeout
        self._root_dir = get_path('.waldorf', abspath=str(Path.home()))
        self._env_dir = get_path('env', abspath=self._root_dir)
        self.update_env_list()

    def update_env_list(self):
        self._env_list = [subdir for subdir in os.listdir(self._env_dir)
                          if os.path.isdir(os.path.join(self._env_dir, subdir))]

    def create_sess(self):
        if self._tmp_sess is not None:
            self._tmp_sess.close()
        self._tmp_sess = PExpect(self.logger,
                                 default_expect=self.default_expect,
                                 default_timeout=self.default_timeout)

    def close_sess(self):
        self._tmp_sess.close()
        self._tmp_sess = None

    def create_env(self, path=None):
        self.logger.info('Create new environment')
        self.create_sess()
        cmd = 'cd ' + self._env_dir
        self.run_cmd(cmd)
        if path:
            cmd = '$HOME/Python/3.6.5/bin/virtualenv -p ' + path + ' ' + self.name
        else:
            cmd = '$HOME/Python/3.6.5/bin/virtualenv ' + self.name
        self.run_cmd(cmd)
        self.update_env_list()
        self.close_sess()
        if self.name not in self._env_list:
            raise Exception('Fail to create virtual environment.')

    def remove_env(self):
        if self.name not in self._env_list:
            raise Exception('Environment can not be found.')
        self.create_sess()
        cmd = 'cd ' + self._env_dir
        self.run_cmd(cmd)
        cmd = 'rm -rf ' + self.name
        self.run_cmd(cmd)
        self.update_env_list()
        self.close_sess()
        assert self.name not in self._env_list
        self.logger.info('Environment {} is removed'.format(self.name))

    def source_env(self):
        cmd = 'source ' + os.path.join(self._env_dir, self.name,
                                       'bin', 'activate')
        result = self.run_cmd(cmd).decode()
        if result.find('No such file or directory') > -1:
            raise Exception('Source can not find the activate file.')

    def check_py_ver(self):
        self.create_sess()
        self.source_env()
        result = self.run_cmd('python -V').decode()
        self.close_sess()
        if result.find('Python') > -1:
            return result
        else:
            raise Exception('Can not find python')

    def git_clone(self, cmd, repo):
        self._tmp_sess.sess.sendline(cmd)
        index = self._tmp_sess.expect([self.default_expect, 'Username for'])
        if index == 0:
            return self._tmp_sess.sess.before
        elif index == 1:
            output = self._tmp_sess.sess.before
            output = output.decode()
            m = re.search("Cloning into '(.*)'\.\.\.", output, flags=re.M)
            folder_name = m.group(1)
            credential = self.get_credential(repo)
            username = credential['Username']
            password = credential['Password']
            self._tmp_sess.sess.sendline(username)
            self._tmp_sess.sess.sendline(password)
            self._tmp_sess.expect(self.default_expect)
            output = self._tmp_sess.sess.before
            output = output.decode()
            m = re.search('Checking connectivity\.\.\. done.',
                          output, flags=re.M)
            if m is None:
                raise Exception('Clone fail')
            self._tmp_sess.run('cd ' + folder_name)
            self._tmp_sess.run('git config credential.helper '
                               '"store --file=.git/.git-credentials"')
            self._tmp_sess.sess.sendline('git pull')
            self._tmp_sess.sess.sendline(username)
            self._tmp_sess.expect('Password for')
            self._tmp_sess.sess.sendline(password)
            self._tmp_sess.expect(self.default_expect)
            self._tmp_sess.run('cd ..')
        else:
            raise Exception('Git clone fail')

    def get_credential(self, repo):
        for k, v in self.git_credential.items():
            if k in repo:
                return v
        raise Exception('Git credential not found')

    def git_cmd(self, cmd, repo):
        self._tmp_sess.sess.sendline(cmd)
        index = self._tmp_sess.expect([self.default_expect, 'Username for'])
        if index == 0:
            return self._tmp_sess.sess.before
        elif index == 1:
            credential = self.get_credential(repo)
            username = credential['Username']
            password = credential['Password']
            self._tmp_sess.sess.sendline(username)
            self._tmp_sess.expect('Password for')
            self._tmp_sess.sess.sendline(password)
            self._tmp_sess.expect(self.default_expect)
        else:
            raise Exception('Git command fail')

    def run_cmd(self, cmd, expect=None):
        if expect:
            return self._tmp_sess.run(cmd, expect)
        else:
            return self._tmp_sess.run(cmd, self.default_expect)

    def get_py_path(self):
        return os.path.join(self._env_dir, self.name, 'bin', 'python')

    def get_env_path(self):
        return os.path.join(self._env_dir, self.name)

    def _run_pair(self, pair):
        return self.__getattribute__(pair.cmd)(*pair.args, **pair.kwargs)

    def check_pattern(self, pairs):
        for pair in pairs:
            res = self._run_pair(pair)
            if isinstance(res, bytes):
                res = res.decode()
            assert pair.pattern is not None
            if re.search(pair.pattern, res, flags=re.M) is None:
                if pair.exist:
                    return False
            else:
                if not pair.exist:
                    return False
        return True

    def run_pairs(self, pairs):
        for p in pairs:
            self._run_pair(p)

    def run_setup_suite(self, suite):
        self.run_pairs(suite.check.setup)
        res = self.check_pattern(suite.check.cmd)
        self.run_pairs(suite.check.cleanup)
        if res:
            return True
        else:
            self.run_pairs(suite.setup.setup)
            self.run_pairs(suite.setup.cmd)
            self.run_pairs(suite.setup.cleanup)
            self.run_pairs(suite.check.setup)
            res = self.check_pattern(suite.check.cmd)
            self.run_pairs(suite.check.cleanup)
            if res:
                return True
            else:
                return False

    def _get_env(self, pairs, suites):
        if pairs[0].cmd != MajorCmd.CREATE_ENV:
            raise Exception('The first pair should be CommandList.CREATE_ENV')
        if pairs[1].cmd != MajorCmd.CHECK_PY_VER:
            raise Exception('The second pair should be '
                            'CommandList.CHECK_PY_VER')
        if self.name in self._env_list:
            if self.check_pattern([pairs[1]]):
                self.logger.info('Success')
            else:
                if self.cfg.env_cfg.already_exist == 'remove':
                    self.remove_env()
                raise Exception(
                    'Environment already exists, but the version of python'
                    ' is not matched with the condition.')
        else:
            self.logger.info('Environment can not be found.')
            self._run_pair(pairs[0])
            if self.check_pattern([pairs[1]]):
                self.logger.info('Success')
            else:
                if self.cfg.env_cfg.version_mismatch == 'remove':
                    self.remove_env()
                raise Exception('The version of python is not matched with'
                                ' the condition.')
        for idx, suite in enumerate(suites):
            if not self.run_setup_suite(suite):
                raise Exception('Setup fail on setup suite {}'.format(idx))

    def get_env(self, pairs, suites):
        """Wrap function, return success or exceptions"""
        try:
            self._get_env(pairs, suites)
            return 0, 'Success'
        except:
            return -1, traceback.format_exc()
