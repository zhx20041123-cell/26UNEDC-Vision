"""
detector.py — YOLOv8 模型加载与推理

可修改参数：
  - _model_path: 模型文件路径（优先 /root/models/maixhub/6992/，否则本地）
  - detect() 的 conf_th: 置信度阈值（默认0.3），越低越容易检出但误检增多
  - detect() 的 iou_th: NMS交并比阈值（默认0.45）
"""

import os
from maix import nn

# 优先使用设备上由 MaixHub 安装的模型；不存在时才使用应用包内的模型。
_model_path = "/root/models/maixhub/6992/model_6992.mud"
if not os.path.exists(_model_path):
    _model_path = "model_6992.mud"

# 模型只在模块首次导入时加载一次，避免在每帧循环中反复占用 NPU 和内存。
_detector = nn.YOLOv8(model=_model_path)


def get_detector():
    return _detector


def detect(img, conf_th=0.3, iou_th=0.45):
    """使用调用方给出的置信度和 NMS 阈值执行一次 YOLOv8 推理。"""
    return _detector.detect(img, conf_th=conf_th, iou_th=iou_th)
