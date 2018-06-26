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

tmux new -s wds -d
tmux send-keys -t wds "cd" Enter
tmux send-keys -t wds "${SOURCE_VENV}" Enter
tmux send-keys -t wds "cd waldorf" Enter
tmux send-keys -t wds "python script/loop_wait.py" Enter
tmux send-keys -t wds "git pull && pip install -U . && \
python -m waldorf.slave -i ${WALDORF_MASTER_IP}" Enter

exit 0