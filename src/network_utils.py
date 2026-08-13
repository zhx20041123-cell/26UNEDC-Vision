"""
network_utils.py — 网络工具函数

无需修改参数。
"""

import socket

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect 不会在这里发送业务数据，只借助系统路由获得本机出口 IP。
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
    except:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip
