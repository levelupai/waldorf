#!/bin/bash

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
SOURCE_VENV="source $WALDORF_HOME/envs/$WALDORF_ENV_NAME/bin/activate"

tmux new -s wdm -d
tmux send-keys -t wdm "cd" Enter
tmux send-keys -t wdm "${SOURCE_VENV}" Enter
tmux send-keys -t wdm "cd waldorf" Enter
tmux send-keys -t wdm "python script/loop_wait.py" Enter
tmux send-keys -t wdm "git pull && pip install -U . && \
python -m waldorf.master -d 1" Enter

exit 0