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
        'virtualenv>=15.2.0',
        'psutil>=5.4.5',
        'aiohttp>=3.1.3',
        'celery>=4.2.1',
        'python-socketio>=1.9.0',
        'tqdm>=4.23.3',
        'socketIO-client>=0.7.2',
        'redis>=2.10.6',
        'pycryptodome>=3.6.6',
        'pexpect>=4.5.0',
        'Markdown>=2.6.11',
        'kombu>=4.2.1',
        'jsonpickle>=1.1'
    ]
)
