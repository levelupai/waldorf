#!/bin/bash
tmux new -s wds -d
tmux send-keys -t wds "cd" Enter
tmux send-keys -t wds "source envs/waldorf/bin/activate" Enter
tmux send-keys -t wds "cd waldorf" Enter
tmux send-keys -t wds "python script/loop_wait.py" Enter
tmux send-keys -t wds "git pull && pip install -U . && \
python -m waldorf.slave -i 127.0.0.1" Enter