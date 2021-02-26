from setuptools import setup, find_packages
import re


def get_property(prop, project):
    result = re.search(r'{}\s*=\s*[\'"]([^\'"]*)[\'"]'.format(prop),
                       open(project + '/__init__.py').read())
    return result.group(1)


setup(
    name='waldorf',
    version=get_property('__version__', 'waldorf'),
    description='Waldorf, a distribution computing package based on celery',
    author='SErAphLi, taibende',
    url='https://github.com/levelupai/waldorf.git',
    packages=find_packages(),
    package_data={'waldorf': ['static/*', 'static/*/*']},
    install_requires=[
        'virtualenv>=16.5.0',
        'psutil>=5.6.2',
        'aiohttp==3.7.4',
        'celery>=4.3.0',
        'python-socketio>=4.0.1',
        'tqdm>=4.23.3',
        'requests>=2.21.0',
        'redis>=3.2.1',
        'pycryptodome>=3.8.1',
        'pexpect>=4.7.0',
        'jsonpickle>=1.1',
        'tabulate>=0.8.3',
    ]
)
