from waldorf.cfg import WaldorfCfg
from waldorf.client import WaldorfClient
from waldorf.env import MajorCmd, MinorCmd, CmdPair, \
    Suite, SetupSuite
import random

MASTER_IP = '127.0.0.1'


def collect_exp(args):
    """An example function

    Generate samples from gym environment.
    The function must only have one argument,
    and you can unpack arguments in the function.
    """
    import gym
    gym.logger.set_level(40)
    env_name, episode = args
    env = gym.make('CartPole-v0')
    exps = []
    for i in range(episode):
        ob = env.reset()
        while True:
            # random agent
            action = env.action_space.sample()
            _ob, reward, done, _ = env.step(action)
            exps.append((ob, action, reward, done, _ob))
            if done:
                break
    env.close()
    # return experience collected by workers
    return exps


class GymDemo(object):
    """
    This is a demonstration about
    how to run gym in waldorf and how to collect results.
    Actually you don't have to install gym first on you local machine.
    """

    def __init__(self):
        self.results = []
        self.setup_waldorf()

    def setup_waldorf(self):
        cfg = WaldorfCfg(master_ip=MASTER_IP)
        cfg.debug = 1
        # Create a Waldorf client and connect it to the Master server
        self.client = WaldorfClient(cfg, limit=50)

        cfg.env_cfg.already_exist = 'remove'
        cfg.env_cfg.version_mismatch = 'remove'
        # If you need git credential to clone repositories,
        # you have to generate the credential file first.
        # This avoid putting any sensitive information directly in your code.
        # cfg.env_cfg.git_credential = open('credential', 'rb').read()

        # Command pair is used to define all sorts of command.
        # These commands are used to create environment
        # and make sure the environment is setup as you want.

        # Paris will have two command pairs.
        # The first is MajorCmd.CREATE_ENV.
        # It declares where waldorf should find the python interpreter.
        # Notice $HOME represent home directory of user running Waldorf.
        # Normally it will be the user named waldorf.
        # The second is MajorCmd.CHECK_PY_VER.
        # It will verify the python interpreter version.
        # Regular expression can be used in the pattern.
        # This two commands can not be changed.
        pairs = [
            CmdPair(MajorCmd.CREATE_ENV,
                    args=['$HOME/Python/3.6.5/bin/python3']),
            CmdPair(MajorCmd.CHECK_PY_VER, pattern='3.6.5')
        ]

        # Setup suites will setup the environment and install requirements.
        # You can use multiple setup suites to make sure the environment
        # is setup as you need.
        suites = [
            SetupSuite(
                Suite([CmdPair(MinorCmd.CREATE_SESS),
                       CmdPair(MinorCmd.SOURCE_ENV),
                       CmdPair(MinorCmd.RUN_CMD, args=['python', '>>>'])],
                      [CmdPair(MinorCmd.RUN_CMD, pattern='No module',
                               exist=False,
                               args=['import gym', '>>>']),
                       CmdPair(MinorCmd.RUN_CMD,
                               args=['gym.__version__', '>>>'],
                               pattern='0.10.5')],
                      [CmdPair(MinorCmd.RUN_CMD, args=['exit()']),
                       CmdPair(MinorCmd.CLOSE_SESS)]),
                Suite([CmdPair(MinorCmd.CREATE_SESS),
                       CmdPair(MinorCmd.SOURCE_ENV)],
                      [CmdPair(MinorCmd.RUN_CMD,
                               args=['pip install gym==0.10.5'])],
                      [CmdPair(MinorCmd.CLOSE_SESS)])
            )
        ]

        print('check_slave', self.client.check_slave())
        print('echo', self.client.echo())

        resp = self.client.get_env('waldorf_gym_test', pairs, suites)
        for hostname, r in resp:
            if r[0] < 0:
                raise Exception(hostname, r[1])

        # Register task on slave
        self.client.reg_task(collect_exp)

        # Freeze will wait until waldorf create a new Celery process
        # After freeze you can not register more task
        self.client.freeze()

    def callback(self, result):
        # Use callback to collect results
        self.results.append(result)

    def play(self):
        # You can use map to run all task
        # It will block until all results are collected
        result = self.client.map(
            collect_exp, [('CartPole-v0', random.randint(20, 50))
                          for i in range(50)])

        # You can also use submit with callback
        # which will let you collect results when the task is done
        for i in range(100):
            episode = random.randint(20, 50)
            self.client.submit(collect_exp, ('CartPole-v0', episode),
                               callback=self.callback)
        # If you use submit wth callback
        # you have use join to wait for all results
        self.client.join()

    def close(self):
        # Although waldorf has mechanism to clean up slave
        # it is recommended to run close after program finished
        # Also you should run close if your program exits with exceptions
        self.client.close()


def main():
    demo = GymDemo()
    demo.play()
    demo.close()


if __name__ == '__main__':
    main()
