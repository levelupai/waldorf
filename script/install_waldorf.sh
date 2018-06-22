#!/bin/bash

if [ $# -lt 1 ]; then
  echo "Required arg: master | slave"
  exit 1
elif [ $1 != "slave" -a $1 != "master" ]; then
  echo "Invalid arg. Require: master | slave"
  exit 1
else
  WALDORF_MODE=$1
fi

## USER DEFINED VARIABLES ##
WALDORF_USER=waldorf
WALDORF_MASTER_IP=127.0.0.1
WALDORF_ENV_NAME=waldorf
WALDORF_GIT_URL=https://github.com/levelupai/waldorf.git
WALDORF_GIT_BRANCH=develop
PIP_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
PYTHON_VERSION=3.6.5
## END OF USER DEFINED VARIABLES


WALDORF_HOME=/home/$WALDORF_USER
VIRTUALENV=Python/$PYTHON_VERSION/bin/virtualenv
PYTHON_INTERPRETER=$WALDORF_HOME/Python/$PYTHON_VERSION/bin/python3
SOURCE_VENV="source $WALDORF_HOME/envs/$WALDORF_ENV_NAME/bin/activate"


# Configure pip mirror settings if the pip config file doesn't already exist
sudo su - $WALDORF_USER -c \
"[ -f ~/.config/pip/pip.conf ] || "\
"(mkdir -p .config/pip && "\
"printf \"[global]\ntimeout = 60\nindex-url = $PIP_MIRROR\" "\
"> ~/.config/pip/pip.conf)"

# Clone the latest Waldorf code from Git repository if it hasn't been done
# already
sudo su - $WALDORF_USER -c \
"[ -d ~/waldorf ] || git clone $WALDORF_GIT_URL"

# Switch to relevant Git code branch
sudo su - $WALDORF_USER -c "cd waldorf && "\
"git checkout -b $WALDORF_GIT_BRANCH origin/$WALDORF_GIT_BRANCH 2>/dev/null"

# Set up crontab (automatically start Waldorf on reboot)
sudo su - $WALDORF_USER -c \
"(printf \"SHELL=/bin/bash\n"\
"@reboot ~/waldorf/script/start_$WALDORF_MODE.sh &\n\") "\
"| sort -r - | uniq - | crontab -"

# Start Waldorf
sudo su - $WALDORF_USER -c "~/waldorf/script/start_$WALDORF_MODE.sh"

exit 0
