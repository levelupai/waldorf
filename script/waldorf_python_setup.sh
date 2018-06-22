#!/bin/bash

# This script downloads the Python interpreter's source code from a
# specified mirror, compiles it and prepares an environment suitable
# for installing Waldorf

## USER DEFINED VARIABLES ##
# Waldorf user name
WALDORF_USER=waldorf
# Waldorf user password (change this!)
WALDORF_USER_PASSWD=123456
# Python version
PYTHON_VERSION=3.6.5
# Python mirror (mainland China)
PYTHON_MIRROR=http://mirrors.sohu.com/python/
# Download URL and details for Python Virtualenv (mainland China mirror)
VIRTUALENV_VERSION=15.2.0
VIRTUALENV_EXTENSION=tar.gz
VIRTUALENV_DOWNLOAD_URL="https://pypi.tuna.tsinghua.edu.cn/packages/b1/72"\
"/2d70c5a1de409ceb3a27ff2ec007ecdd5cc52239e7c74990e32af57affe9/"\
"virtualenv-15.2.0.tar.gz#md5=b5f6b473140cc627d19d0d203f3b63cc"
## END OF USER DEFINED VARIABLES ##



WALDORF_HOME=/home/$WALDORF_USER
PYTHON_URL="${PYTHON_MIRROR}${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz"
PYTHON_SOURCE_DIR=$WALDORF_HOME/Python/__PYTHON_SOURCE__
PYTHON_BUILD_DIR=$PYTHON_SOURCE_DIR/Python-${PYTHON_VERSION}
PYTHON_INSTALL_DIR=$WALDORF_HOME/Python/${PYTHON_VERSION}
PYTHON_VIRTUALENVS_DIR=$WALDORF_HOME/envs

# Create Waldorf user
if ! id -u $WALDORF_USER > /dev/null 2>&1; then
    echo "Waldorf user does not exist. Creating..."
    sudo useradd -m $WALDORF_USER -s /bin/bash
    sudo sh -c "echo $WALDORF_USER:$WALDORF_USER_PASSWD | chpasswd"
else
    echo "Waldorf user already exists. Skipping..."
fi

# Download Python interpreter source code
sudo su - $WALDORF_USER -c "mkdir -p $PYTHON_SOURCE_DIR"
sudo su - $WALDORF_USER -c "mkdir -p $PYTHON_INSTALL_DIR"
sudo su - $WALDORF_USER -c "cd $PYTHON_SOURCE_DIR && "\
"wget $PYTHON_URL"
#Download Virtualenv too but don't build it yet
sudo su - $WALDORF_USER -c "cd $PYTHON_SOURCE_DIR && "\
"wget $VIRTUALENV_DOWNLOAD_URL"
# Unpack and build Python interpreter
sudo su - $WALDORF_USER -c "cd $PYTHON_SOURCE_DIR && "\
"tar xf Python-${PYTHON_VERSION}.tgz"
sudo su - $WALDORF_USER -c "cd $PYTHON_BUILD_DIR && "\
"./configure --enable-optimizations --prefix=$PYTHON_INSTALL_DIR"
sudo su - $WALDORF_USER -c "cd $PYTHON_BUILD_DIR && "\
"make"
sudo su - $WALDORF_USER -c "cd $PYTHON_BUILD_DIR && "\
"make test"
sudo su - $WALDORF_USER -c "cd $PYTHON_BUILD_DIR && "\
"make install"

# Install Virtualenv
PYTHON_INTERPRETER_BIN="$PYTHON_INSTALL_DIR/bin/"\
"`ls $PYTHON_INSTALL_DIR/bin | grep python | head -1`"
sudo su - $WALDORF_USER -c "cd $PYTHON_SOURCE_DIR && "\
"tar xf virtualenv-$VIRTUALENV_VERSION.$VIRTUALENV_EXTENSION"
sudo su - $WALDORF_USER -c "cd $PYTHON_SOURCE_DIR/virtualenv-"\
"$VIRTUALENV_VERSION && $PYTHON_INTERPRETER_BIN ./setup.py install"
sudo su - $WALDORF_USER -c "$PYTHON_INSTALL_DIR/bin/virtualenv "\
"-p $PYTHON_INTERPRETER_BIN $PYTHON_VIRTUALENVS_DIR/$WALDORF_USER"

# Tidy up permissions if needed
sudo chown -R $WALDORF_USER:$WALDORF_USER $WALDORF_HOME
