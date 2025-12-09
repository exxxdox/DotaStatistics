#!/bin/bash
# 激活虚拟环境
cd /root/workspace/DotaStatistics || exit

. myenv/bin/activate

python3 main.py

deactivate
