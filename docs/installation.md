# Waldorf Installation Guide

## Introduction

This is a simple installation guide for Ubuntu 16.04 LTS. It should also work
on any recent Debian-based system.

Non-Debian Linux distributions (e.g. CentOS, Arch) may require minor
modifications (i.e. replace ''apt-get'' with your system's package manager).

For simplicity, the entire Waldorf stack is assumed to be on a single machine.

## Prerequisites

Waldorf requires a Linux environment and Python 3.5.3 or above (Python 3.6 is
recommended).

The included setup script creates a new Linux user and downloads/compiles a
recent Python interpreter, which provides a suitable environment for installing
Waldorf.

First, install the necessary C build tools. You will need to log into a user
account that can has **sudo** privileges:

```bash
# install C build tools
sudo apt-get install make gcc

# install C development headers for a full Python install
sudo apt-get install libbz2-dev libgdbm-dev liblzma-dev libncurses5-dev \
libreadline-dev libsqlite3-dev libssl-dev tk-dev zlib1g-dev

# install other packages needed by Waldorf
sudo apt-get install libmemcached-dev git tmux
```

Waldorf, like Celery, needs a message broker to send messages to worker
processes. A backend data store is also required if your tasks will return
output values.

By default, Waldorf uses the Redis in-memory data store as both a message
broker and backend. However, support is also available for RabbitMQ (broker)
and Memcached (backend).

To install Redis:

```bash
sudo apt-get install redis-server
```

The Redis configuration file can be found at `/etc/redis/redis.conf` on
Debian-based systems. The default configuration is good enough for this
tutorial.

In a production environment, performance testing of the message broker and
backend are **strongly** encouraged to get reasonable performance.

If you prefer to use RabbitMQ as a message broker:

```bash
sudo apt-get install rabbitmq-server
# Create a user (waldorf) with password 'waldorf'
sudo rabbitmqctl add_user waldorf waldorf
sudo rabbitmqctl set_permissions -p / waldorf ".*" ".*" ".*"
```

And if you want to use Memcached as a backend data store:
```bash
sudo apt-get install memcached
```

When the Waldorf master and slave processes are started, the broker and
backend can be specified using the `--broker` and `--backend`
arguments.

## Install a suitable Python environment

If you are an experienced Python user, feel free to modify the setup script
for your own needs.

```bash
mkdir waldorf_install
cd waldorf_install
git clone https://github.com/levelup-ai/waldorf.git
cd waldorf
# write a log
script/waldorf_python_setup.sh | tee python_setup_log
```

## Install the Waldorf master

```bash
script/install_waldorf.sh master | tee waldorf_master_setup_log
```

The install script starts up a [tmux](https://www.tmuxcheatsheet.com/) session
from the Waldorf user account. To view it:

```bash
su waldorf
# Enter the default password: 123456
tmux a -t wdm
# to exit, Ctrl+b then d
```

## Install a Waldorf slave

If you are still in the Waldorf master's tmux session, exit by using Ctrl+b
then d.

Return to your user account with **sudo** permissions (Ctrl + d)

```bash
script/install_waldorf.sh slave | tee waldorf_slave_setup_log
```

If you want to see the progress of the slave's installation:

```bash
su waldorf
# Enter the default password: 123456
tmux a -t wds
# to exit, Ctrl+b then d
# to switch to a different tmux session, Ctrl+b then (
```

## Monitor your cluster

The Waldorf master listens on port 61801 and accepts HTTP and WebSocket
connections.

You can view the cluster administration page at [http://127.0.0.1:61801/]
(http://127.0.0.1:61801/)

## Run the client demo

The gym demo can be run as follows:

```bash
git clone https://github.com/levelup-ai/waldorf.git
cd waldorf
# enter a suitable Python virtualenv...
pip install -U .
pip install gym==0.10.5
```

Now you can run [the gym demo](../example/gym_demo.py).