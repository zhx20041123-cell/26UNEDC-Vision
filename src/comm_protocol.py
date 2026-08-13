"""
comm_protocol.py — UART0 视觉结果发送

数据包格式（固定 12 字节，大端序）:
  [0]  0xA5              帧头1
  [1]  0x5A              帧头2
  [2]  0x01              协议版本
  [3]  0x01              消息类型：视觉目标
  [4]  0x05              负载长度
  [5]  sequence          包序号，0~255 循环
  [6]  flags             bit0=1 表示目标有效
  [7]  center_x 高8位
  [8]  center_x 低8位
  [9]  score             识别分数，0~100
  [10] CRC16 高8位
  [11] CRC16 低8位

CRC 使用 CRC-16/CCITT-FALSE，计算范围为 [2]~[9]。
"""
from maix import uart, pinmap, err

UART_DEV = "/dev/ttyS0"
UART_BAUD = 115200
TX_PIN = "A16"
RX_PIN = "A17"

FRAME_HEADER = bytes([0xA5, 0x5A])
PROTOCOL_VERSION = 0x01
MESSAGE_TYPE_VISION = 0x01
PAYLOAD_LENGTH = 0x05
FLAG_TARGET_VALID = 0x01
PACKET_SIZE = 12

# ---- 初始化 UART0 ----
err.check_raise(
    pinmap.set_pin_function(TX_PIN, "UART0_TX"),
    "Failed to configure UART0 TX pin",
)
err.check_raise(
    pinmap.set_pin_function(RX_PIN, "UART0_RX"),
    "Failed to configure UART0 RX pin",
)
serial_dev = uart.UART(UART_DEV, UART_BAUD)


def crc16_ccitt(data):
    """CRC-16/CCITT-FALSE：poly=0x1021，init=0xFFFF。"""
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_vision_packet(sequence, valid, center_x, score, img_width):
    """构造带序号、有效位、识别分数和 CRC16 的视觉结果包。"""
    if valid:
        # 坐标属于模型输入图像，不是 WebRTC 原始 640×480 画面。
        center_x = max(0, min(img_width - 1, int(center_x)))
        score = max(0, min(100, int(score)))
        flags = FLAG_TARGET_VALID
    else:
        # 无目标时坐标和分数必须清零，主控只根据 valid 位决定是否使用。
        center_x = 0
        score = 0
        flags = 0

    payload = bytes([
        sequence & 0xFF,
        flags,
        (center_x >> 8) & 0xFF,
        center_x & 0xFF,
        score,
    ])
    crc_data = bytes([
        PROTOCOL_VERSION,
        MESSAGE_TYPE_VISION,
        PAYLOAD_LENGTH,
    ]) + payload
    crc = crc16_ccitt(crc_data)
    return FRAME_HEADER + crc_data + bytes([
        (crc >> 8) & 0xFF,
        crc & 0xFF,
    ])


def _write_packet(packet):
    """处理 UART 短写；最多补写一次，避免通信故障卡住视觉主循环。"""
    total_sent = 0
    for _ in range(2):
        sent = serial_dev.write(packet[total_sent:])
        if sent <= 0:
            break
        total_sent += sent
        if total_sent == len(packet):
            return True

    print(f"UART packet incomplete: {total_sent}/{len(packet)} bytes")
    return False


_sequence = 0
_last_cx = 0      # 上一帧有效目标的中心 X，无目标时复用
_last_score = 0   # 上一帧有效目标的分数，无目标时复用

def send_best_x(objs, img_width):
    """有目标时更新缓存并发送；无目标时复用上一帧有效数据，始终发 valid=1 包。"""
    global _sequence, _last_cx, _last_score

    if objs:
        # 多个钢球同时出现时，只向控制板发送模型评分最高的一个。
        best = max(objs, key=lambda obj: obj.score)
        _last_cx = best.x + best.w // 2
        _last_score = int(best.score * 100 + 0.5)

    packet = build_vision_packet(
        _sequence,
        True,           # 始终 valid=True，无目标时复用缓存坐标
        _last_cx,
        _last_score,
        img_width,
    )
    _sequence = (_sequence + 1) & 0xFF
    return _write_packet(packet)
