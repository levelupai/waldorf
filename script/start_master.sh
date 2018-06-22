#!/bin/bash
tmux new -s wdm -d
tmux send-keys -t wdm "cd" Enter
tmux send-keys -t wdm "source envs/waldorf/bin/activate" Enter
tmux send-keys -t wdm "cd waldorf" Enter
tmux send-keys -t wdm "python script/loop_wait.py" Enter
tmux send-keys -t wdm "git pull && pip install -U . && \
python -m waldorf.master -d 1" Enter