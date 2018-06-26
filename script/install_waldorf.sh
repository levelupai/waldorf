#!/bin/bash

# read args
if [ $# -lt 1 ]; then
  echo "Required arg: master | slave"
  exit 1
elif [ $1 != "slave" -a $1 != "master" ]; then
  echo "Invalid arg. Require: master | slave"
  exit 1
else
  WALDORF_MODE=$1
fi


# Import configuration variables
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Check config file exists
[ -f ${SCRIPT_DIR}/config.sh ] || \
echo "You must create a config.sh file in the script directory." \
"See config.sh.example in the script directory for a reference."
# Exit if config file not found
[ -f ${SCRIPT_DIR}/config.sh ] || exit 1
# Load config
source ${SCRIPT_DIR}/config.sh


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

# Copy config.sh to Waldorf user's directory
sudo cp ${SCRIPT_DIR}/config.sh $WALDORF_HOME/waldorf/script/
# Change file ownership to Waldorf user
sudo chown -R $WALDORF_USER:$WALDORF_USER $WALDORF_HOME/waldorf/script/

# Set up crontab (automatically start Waldorf on reboot)
USER_CRONTAB=`sudo su - $WALDORF_USER -c "crontab -l" 2>/dev/null`
# User already has crontab file
if [ $? = 0 ]; then
  sudo su - $WALDORF_USER -c \
"(printf \"SHELL=/bin/bash\n"\
"@reboot ~/waldorf/script/start_$WALDORF_MODE.sh &\n\"; crontab -l) "\
"| sort -r - | uniq - | crontab -"
else
  sudo su - $WALDORF_USER -c \
"(printf \"SHELL=/bin/bash\n"\
"@reboot ~/waldorf/script/start_$WALDORF_MODE.sh &\n\";) "\
"| sort -r - | uniq - | crontab -"
fi

# Start Waldorf
sudo su - $WALDORF_USER -c "~/waldorf/script/start_$WALDORF_MODE.sh"

exit 0
