#!/usr/bin/env python3
"""
钉钉机器人通知脚本

用法: dingtalk_notify.py <success|failure> <消息内容>
"""

import os
import sys
import subprocess
import json
from datetime import datetime

# 配置变量
DINGTALK_WEBHOOK_ENABLED = os.environ.get("DINGTALK_WEBHOOK_ENABLED", "false").lower() == "true"
DINGTALK_WEBHOOK_URL = os.environ.get("DINGTALK_WEBHOOK_URL", "")

def log(message: str, is_error: bool = False):
    """记录日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    output = sys.stderr if is_error else sys.stdout
    print(f"[{timestamp}] {message}", file=output)

def main():
    """主函数"""
    # 如果未启用，直接退出
    if not DINGTALK_WEBHOOK_ENABLED:
        sys.exit(0)
    
    # 如果未配置 webhook URL，直接退出
    if not DINGTALK_WEBHOOK_URL:
        log("警告: 钉钉通知已启用但未配置 DINGTALK_WEBHOOK_URL，跳过通知", is_error=True)
        sys.exit(0)
    
    # 参数
    status = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    message = sys.argv[2] if len(sys.argv) > 2 else ""
    
    # 获取主机名和容器名
    hostname = os.environ.get("HOSTNAME", "")
    if not hostname:
        try:
            hostname = subprocess.run(
                ["hostname"],
                capture_output=True,
                text=True,
                check=True
            ).stdout.strip()
        except Exception:
            hostname = "unknown"
    
    container_name = os.environ.get("CONTAINER_NAME", "mysql8044")
    
    # 根据状态设置标题和颜色
    if status == "success":
        title = "✅ MySQL 备份成功"
        color = "#00FF00"
    elif status == "failure":
        title = "❌ MySQL 备份失败"
        color = "#FF0000"
    else:
        title = "ℹ️ MySQL 备份通知"
        color = "#0000FF"
    
    # 构建消息内容
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = message if message else "备份操作已完成"
    
    # 构建 JSON 消息体
    json_payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"## {title}\n\n**时间**: {timestamp}\n\n**主机**: {hostname}\n\n**容器**: {container_name}\n\n**详情**:\n{full_message}"
        }
    }
    
    # 发送通知
    try:
        import urllib.request
        import urllib.parse
        
        data = json.dumps(json_payload).encode('utf-8')
        req = urllib.request.Request(
            DINGTALK_WEBHOOK_URL,
            data=data,
            headers={'Content-Type': 'application/json'}
        )
        
        with urllib.request.urlopen(req, timeout=10) as response:
            http_code = response.getcode()
            body = response.read().decode('utf-8')
            
            # 检查响应
            if http_code == 200:
                # 检查响应内容
                try:
                    response_data = json.loads(body)
                    if response_data.get("errcode") == 0:
                        log("钉钉通知发送成功", is_error=True)
                        sys.exit(0)
                    else:
                        log(f"钉钉通知发送失败: {body}", is_error=True)
                        sys.exit(1)
                except json.JSONDecodeError:
                    if '"errcode":0' in body:
                        log("钉钉通知发送成功", is_error=True)
                        sys.exit(0)
                    else:
                        log(f"钉钉通知发送失败: {body}", is_error=True)
                        sys.exit(1)
            else:
                log(f"钉钉通知发送失败，HTTP 状态码: {http_code}, 响应: {body}", is_error=True)
                sys.exit(1)
    except Exception as e:
        log(f"钉钉通知发送失败: {e}", is_error=True)
        sys.exit(1)

if __name__ == "__main__":
    main()

