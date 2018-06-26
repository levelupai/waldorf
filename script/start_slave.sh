#!/bin/bash

# import configuration variables
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
source ${SCRIPT_DIR}/config.sh

tmux new -s wds -d
tmux send-keys -t wds "cd" Enter
tmux send-keys -t wds "source envs/waldorf/bin/activate" Enter
tmux send-keys -t wds "cd waldorf" Enter
tmux send-keys -t wds "python script/loop_wait.py" Enter
tmux send-keys -t wds "git pull && pip install -U . && \
python -m waldorf.slave -i ${WALDORF_MASTER_IP}" Enter
