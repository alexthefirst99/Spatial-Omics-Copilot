#!/bin/bash


pkill -f index_app.py

nohup python3 index_app.py > launcher.log 2>&1 &

