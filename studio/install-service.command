#!/bin/zsh
cd "${0:A:h}" || exit 1
../.venv/bin/python service.py install
echo
read -k1 "?Bấm phím bất kỳ để đóng."
