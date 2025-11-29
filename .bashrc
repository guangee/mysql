#!/bin/bash

if [ -t 1 ]; then
	export PS1="\e[1;34m[\e[1;33m\u@\e[1;32mdocker-\h\e[1;37m:\w\[\e[1;34m]\e[1;36m\\$ \e[0m"
fi

# Aliases
alias l='ls -lAsh --color'
alias ls='ls -C1 --color'
alias cp='cp -ip'
alias rm='rm -i'
alias mv='mv -i'
alias h='cd ~;clear;'

. /etc/os-release

echo -e -n '\E[1;34m'
figlet -w 120 "TulanTech"
echo -e "\E[1;36mMYSQL_VERSION    \E[1;32m${MYSQL_VERSION:-unknown}\e[0m"
echo -e -n '\E[1;34m'
echo "Base: ${PRETTY_NAME:-linux/amd64}"
echo -e '\E[0m'

# 显示备份工具帮助信息（仅在交互式 shell 中显示）
if [ -t 1 ] && [ -f /scripts/main.py ]; then
    echo ""
    echo -e "\E[1;33m═══════════════════════════════════════════════════════════════════════════════\e[0m"
    echo -e "\E[1;36m💡 提示: 使用 'python3 /scripts/main.py help' 查看备份恢复工具使用帮助\e[0m"
    echo -e "\E[1;33m═══════════════════════════════════════════════════════════════════════════════\e[0m"
    echo ""
fi
