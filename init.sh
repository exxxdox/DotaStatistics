#!/bin/bash

apt install python3.12-venv -y

python3 -m venv myenv

. myenv/bin/activate

pip install -r requirement.txt

cp ./service/dota.service /etc/systemd/system/dota.service

systemctl enable dota.service

systemctl start dota.service

systemctl status dota.service