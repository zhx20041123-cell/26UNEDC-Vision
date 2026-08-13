"""
main.py — 主流程：摄像头采集 + YOLO检测 + 本机显示 + 串口上报

调试阶段：屏幕+触摸调参（曝光/对比度/置信度），按 SAVE→RUN 保存并进入运行阶段。
运行阶段：关闭屏幕，纯检测+UART 全速输出。

可修改参数：
  - INFER_EVERY_N: 推理间隔（帧数），1=每帧推理，2=隔帧推理，越大越省算力
  - crop_ratio_h: 垂直裁剪比例（0~1），0.2=保留中间20%，裁剪上下黑边区
  - crop_ratio_w: 水平裁剪比例（0~1），1=水平完整保留
  - DISPLAY_INTERVAL_MS: 本机屏幕刷新间隔（毫秒）
  - 识别框颜色: 搜索 COLOR_GREEN 可改颜色
  - 识别框线宽: 搜索 thickness=2 可改线宽
"""

from maix import camera, display, image, app, time, touchscreen
import threading

from comm_protocol import send_best_x
from detector import get_detector, detect as model_detect
from network_utils import get_ip
import web_ui

# ---- MaixCAM Pro 触摸调参 ----
# 所有调节值都只在本次运行中生效，重启后恢复下面的初始值。
EXPOSURE_MIN_US = 500
EXPOSURE_MAX_US = 30000
EXPOSURE_STEP_US = 500
CONTRAST_MIN = 0
CONTRAST_MAX = 100
CONTRAST_STEP = 5
CONF_THRESHOLD_MIN = 0.10
CONF_THRESHOLD_MAX = 0.90
CONF_THRESHOLD_STEP = 0.05
current_conf_threshold = 0.10

# ---- 摄像头 ----
# 主通道 640x480 用于推流，检测通过子通道降采样到模型尺寸。
detect_w = get_detector().input_width()
detect_h = get_detector().input_height()
cam = camera.Camera(640, 480, image.Format.FMT_RGB888, fps=60)
cam.exp_mode(camera.AeMode.Manual)
current_exposure_us = cam.exposure(13200)
cam.gain(200)
current_contrast = cam.constrast(90)

# 检测子通道：从 640x480 传感器的硬件缩放器降到模型输入尺寸
cam_detect = cam.add_channel(detect_w, detect_h)

# ---- 启动 Flask HTTP 服务（独立线程） ----
flask_thread = threading.Thread(target=web_ui.start_flask, args=(8080,), daemon=True)
flask_thread.start()
http_url = f"http://{get_ip()}:8080"
print(f"HTTP 页面地址: {http_url}")

# ---- 显示与触摸 ----
dis = display.Display()
ts = touchscreen.TouchScreen()
CHINESE_FONT = "sourcehansans"
# MaixPy 默认字体只支持英文，中文界面必须加载系统自带的思源黑体。
image.load_font(
    CHINESE_FONT,
    "/maixapp/share/font/SourceHanSansCN-Regular.otf",
    size=12,
)

DISPLAY_WIDTH = 320
DISPLAY_HEIGHT = 240
DISPLAY_INTERVAL_MS = 200

BUTTON_MARGIN = 4
BUTTON_GAP = 4
BUTTON_HEIGHT = 31
PARAM_LABEL_HEIGHT = 14
PANEL_HEIGHT = BUTTON_HEIGHT + PARAM_LABEL_HEIGHT + BUTTON_MARGIN * 2
BUTTON_LABELS = ("EXP-", "EXP+", "CON-", "CON+")
SAVE_BUTTON_HEIGHT = 36
SAVE_BUTTON_MARGIN = 4
CONF_PANEL_WIDTH = 92
CONF_PANEL_HEIGHT = 54
CONF_BUTTON_MARGIN = 2
CONF_BUTTON_GAP = 2
CONF_BUTTON_HEIGHT = 18
CONF_BUTTON_LABELS = ("阈值-", "阈值+")
CENTER_X_COLOR = image.Color.from_rgb(255, 215, 0)
touch_pressed = False

# ---- 阶段管理 ----
PHASE_DEBUG = "debug"
PHASE_RUN = "run"
phase = PHASE_DEBUG  # 启动默认进入调试阶段，可触摸调参

def save_and_run():
    """保留本次内存中的调参值，并切换到运行阶段（关闭屏幕）。"""
    global phase
    phase = PHASE_RUN
    print(
        ">>> Switched to RUN phase, display off: "
        f"exposure={current_exposure_us}us, "
        f"contrast={current_contrast}, "
        f"conf={current_conf_threshold:.2f} <<<"
    )


def clamp(value, minimum, maximum):
    """把调节值限制在硬件和界面允许的范围内。"""
    return max(minimum, min(maximum, value))


def get_button_rects(screen_width, screen_height):
    """根据实际屏幕尺寸计算底部四个调参按钮，避免写死触摸坐标。"""
    button_width = (screen_width - BUTTON_MARGIN * 2 - BUTTON_GAP * 3) // 4
    button_y = screen_height - BUTTON_HEIGHT - BUTTON_MARGIN
    return [
        (
            BUTTON_MARGIN + index * (button_width + BUTTON_GAP),
            button_y,
            button_width,
            BUTTON_HEIGHT,
        )
        for index in range(4)
    ]


def get_save_button_rect(screen_width, screen_height):
    """计算底部 SAVE→RUN 按钮的矩形，横跨整个宽度放在调参按钮上方。"""
    save_y = screen_height - BUTTON_HEIGHT - SAVE_BUTTON_HEIGHT - SAVE_BUTTON_MARGIN * 2
    return (
        BUTTON_MARGIN,
        save_y,
        screen_width - BUTTON_MARGIN * 2,
        SAVE_BUTTON_HEIGHT,
    )


def get_conf_button_rects(screen_width):
    """计算右上角两个阈值按钮的显示区域和触摸区域。"""
    panel_width = min(CONF_PANEL_WIDTH, screen_width)
    panel_x = screen_width - panel_width
    button_width = (
        panel_width - CONF_BUTTON_MARGIN * 2 - CONF_BUTTON_GAP
    ) // 2
    button_y = CONF_PANEL_HEIGHT - CONF_BUTTON_HEIGHT - CONF_BUTTON_MARGIN
    return [
        (
            panel_x + CONF_BUTTON_MARGIN
            + index * (button_width + CONF_BUTTON_GAP),
            button_y,
            button_width,
            CONF_BUTTON_HEIGHT,
        )
        for index in range(2)
    ]


def point_in_rect(x, y, rect):
    """判断触摸点是否落在指定按钮矩形内。"""
    rect_x, rect_y, rect_w, rect_h = rect
    return rect_x <= x < rect_x + rect_w and rect_y <= y < rect_y + rect_h


def apply_tuning_button(button_index):
    """把底部按钮操作立即应用到摄像头 ISP。"""
    global current_exposure_us, current_contrast

    if button_index == 0:
        target = clamp(
            current_exposure_us - EXPOSURE_STEP_US,
            EXPOSURE_MIN_US,
            EXPOSURE_MAX_US,
        )
        current_exposure_us = cam.exposure(target)
    elif button_index == 1:
        target = clamp(
            current_exposure_us + EXPOSURE_STEP_US,
            EXPOSURE_MIN_US,
            EXPOSURE_MAX_US,
        )
        current_exposure_us = cam.exposure(target)
    elif button_index == 2:
        target = clamp(
            current_contrast - CONTRAST_STEP,
            CONTRAST_MIN,
            CONTRAST_MAX,
        )
        current_contrast = cam.constrast(target)
    elif button_index == 3:
        target = clamp(
            current_contrast + CONTRAST_STEP,
            CONTRAST_MIN,
            CONTRAST_MAX,
        )
        current_contrast = cam.constrast(target)

    print(f"Camera tuning: exposure={current_exposure_us}us, contrast={current_contrast}")


def apply_conf_button(button_index):
    """调整检测结果的最低通过门槛，不会改变模型自身输出的分数。"""
    global current_conf_threshold

    if button_index == 0:
        current_conf_threshold = clamp(
            current_conf_threshold - CONF_THRESHOLD_STEP,
            CONF_THRESHOLD_MIN,
            CONF_THRESHOLD_MAX,
        )
    elif button_index == 1:
        current_conf_threshold = clamp(
            current_conf_threshold + CONF_THRESHOLD_STEP,
            CONF_THRESHOLD_MIN,
            CONF_THRESHOLD_MAX,
        )

    current_conf_threshold = round(current_conf_threshold, 2)
    print(f"Detection threshold: {current_conf_threshold:.2f}")


def handle_touch():
    """非阻塞读取触摸事件，并在手指松开时只触发一次按钮。"""
    global touch_pressed

    # 没有新事件就立即返回，避免触摸读取拖慢摄像头主循环。
    if not ts.available(0):
        return

    touch_x, touch_y, pressed = ts.read()
    if pressed:
        # 按下阶段只记录状态，防止长按导致参数连续跳变。
        touch_pressed = True
        return

    if not touch_pressed:
        return

    touch_pressed = False

    # 触摸坐标是物理屏幕坐标，需映射到 320×240 绘制坐标系。
    scale_x = DISPLAY_WIDTH / dis.width()
    scale_y = DISPLAY_HEIGHT / dis.height()
    draw_x = int(touch_x * scale_x)
    draw_y = int(touch_y * scale_y)

    # 优先检查 SAVE→RUN 按钮，面积最大放在最前面
    if point_in_rect(draw_x, draw_y, get_save_button_rect(DISPLAY_WIDTH, DISPLAY_HEIGHT)):
        save_and_run()
        return

    for index, rect in enumerate(get_button_rects(DISPLAY_WIDTH, DISPLAY_HEIGHT)):
        if point_in_rect(draw_x, draw_y, rect):
            apply_tuning_button(index)
            return

    for index, rect in enumerate(get_conf_button_rects(DISPLAY_WIDTH)):
        if point_in_rect(draw_x, draw_y, rect):
            apply_conf_button(index)
            return


def draw_tuning_panel(img_to_show, best_score, best_center_x):
    """把检测画面、参数文字和按钮合成为最终的本机屏幕画面。"""
    screen_width = DISPLAY_WIDTH
    screen_height = DISPLAY_HEIGHT
    # 只在紧凑画布上绘制，随后贴到全屏黑色画布的左上角。
    screen_img = img_to_show.resize(
        screen_width,
        screen_height,
    )

    # ---- SAVE→RUN 按钮 ----
    save_rect = get_save_button_rect(screen_width, screen_height)
    sx, sy, sw, sh = save_rect
    screen_img.draw_rect(sx, sy, sw, sh, color=image.Color.from_rgb(0, 100, 220), thickness=-1)
    screen_img.draw_string(
        sx + sw // 2 - 40,
        sy + sh // 2 - 8,
        "SAVE & RUN",
        color=image.COLOR_WHITE,
        scale=1.0,
    )

    # ---- 底部调参面板 ----
    panel_y = screen_height - PANEL_HEIGHT
    screen_img.draw_rect(
        0,
        panel_y,
        screen_width,
        PANEL_HEIGHT,
        color=image.COLOR_BLACK,
        thickness=-1,
    )

    button_rects = get_button_rects(screen_width, screen_height)
    param_label_y = panel_y + BUTTON_MARGIN
    screen_img.draw_string(
        button_rects[0][0],
        param_label_y,
        f"曝光:{current_exposure_us}",
        color=image.COLOR_WHITE,
        font=CHINESE_FONT,
    )
    screen_img.draw_string(
        button_rects[2][0],
        param_label_y,
        f"对比:{current_contrast}",
        color=image.COLOR_WHITE,
        font=CHINESE_FONT,
    )

    button_colors = (
        image.COLOR_RED,
        image.COLOR_GREEN,
        image.COLOR_RED,
        image.COLOR_GREEN,
    )
    for index, rect in enumerate(button_rects):
        button_x, button_y, button_w, button_h = rect
        screen_img.draw_rect(
            button_x,
            button_y,
            button_w,
            button_h,
            color=button_colors[index],
            thickness=-1,
        )
        screen_img.draw_string(
            button_x + 8,
            button_y + 8,
            BUTTON_LABELS[index],
            color=image.COLOR_WHITE,
            scale=1.2,
        )

    # ---- 右上角置信度面板 ----
    confidence_text = "分数:--"
    if best_score is not None:
        confidence_text = f"分数:{best_score * 100:.1f}%"
    conf_panel_width = min(CONF_PANEL_WIDTH, screen_width)
    conf_panel_x = screen_width - conf_panel_width
    screen_img.draw_rect(
        conf_panel_x,
        0,
        conf_panel_width,
        CONF_PANEL_HEIGHT,
        color=image.COLOR_BLACK,
        thickness=-1,
    )
    screen_img.draw_string(
        conf_panel_x + CONF_BUTTON_MARGIN,
        4,
        confidence_text,
        color=image.COLOR_WHITE,
        font=CHINESE_FONT,
    )
    screen_img.draw_string(
        conf_panel_x + CONF_BUTTON_MARGIN,
        18,
        f"阈值:{current_conf_threshold * 100:.0f}%",
        color=image.COLOR_WHITE,
        font=CHINESE_FONT,
    )
    center_x_text = "X:--"
    if best_center_x is not None:
        center_x_text = f"X:{best_center_x}"
    screen_img.draw_string(
        conf_panel_x + CONF_BUTTON_MARGIN,
        32,
        center_x_text,
        color=CENTER_X_COLOR,
        font=CHINESE_FONT,
    )

    conf_button_colors = (image.COLOR_RED, image.COLOR_GREEN)
    for index, rect in enumerate(get_conf_button_rects(screen_width)):
        button_x, button_y, button_w, button_h = rect
        screen_img.draw_rect(
            button_x,
            button_y,
            button_w,
            button_h,
            color=conf_button_colors[index],
            thickness=-1,
        )
        screen_img.draw_string(
            button_x + 4,
            button_y + 4,
            CONF_BUTTON_LABELS[index],
            color=image.COLOR_WHITE,
            font=CHINESE_FONT,
        )
    return screen_img


INFER_EVERY_N = 1
frame_count = 0

# 推流跳帧：60fps 下每 9 帧推一帧 ≈ 6.7fps，接近目标 7fps
STREAM_EVERY_N = 9

H = get_detector().input_height()
W = get_detector().input_width()

fps_timer = time.ticks_ms()
fps = 0

while not app.need_exit():
    # 调试阶段：处理触摸输入
    if phase == PHASE_DEBUG:
        handle_touch()

    img = cam_detect.read()
    frame_count += 1

    # 每帧消费主通道（避免缓冲区满），只在跳帧时编码 JPEG 用于推流
    try:
        stream_img = cam.read()
        if frame_count % STREAM_EVERY_N == 0:
            jpeg_img = stream_img.to_jpeg(quality=70)
            web_ui.latest_jpeg = jpeg_img.to_bytes()
    except Exception:
        pass

    # 只保留模型画面中央区域，其余位置填黑，减少场地上方和下方的干扰。
    crop_ratio_h = 0.10
    crop_ratio_w = 1
    crop_center_y_ratio = 0.560
    crop_h = int(H * crop_ratio_h)
    crop_w = int(W * crop_ratio_w)
    offset_h = int(H * crop_center_y_ratio - crop_h / 2)
    offset_w = (W - crop_w) // 2
    img_crop = image.Image(crop_w, crop_h, img.format())
    img_crop.draw_image(-offset_w, -offset_h, img)
    # 把裁剪内容放回原坐标，保证检测框和串口 X 坐标仍属于模型输入坐标系。
    img = image.Image(W, H, img.format(), bg=image.COLOR_BLACK)
    img.draw_image(offset_w, offset_h, img_crop)

    objs = []
    if frame_count % INFER_EVERY_N == 0:
        # 阈值越低越容易检出，也更容易误检；IOU 阈值用于删除重叠检测框。
        objs = model_detect(img, conf_th=current_conf_threshold, iou_th=0.45)
        send_best_x(objs, W)

    if phase == PHASE_DEBUG:
        # ---- 调试阶段：画检测框、FPS、调参面板，show 到屏幕 ----
        # 左上角：IP 地址 + 阶段标签
        img.draw_string(5, 5, http_url, color=image.COLOR_WHITE, scale=0.6)
        img.draw_string(5, 20, "DEBUG", color=image.COLOR_YELLOW, scale=0.7)

        for obj in objs:
            img.draw_rect(obj.x, obj.y, obj.w, obj.h, color=image.COLOR_GREEN, thickness=2)

        if time.ticks_ms() - fps_timer > 1000:
            fps = frame_count * 1000 // (time.ticks_ms() - fps_timer)
            fps_timer = time.ticks_ms()
            frame_count = 0
        img.draw_string(DISPLAY_WIDTH - 55, 5, f"FPS:{fps}", color=image.COLOR_GREEN, scale=0.7)

        best_obj = max(objs, key=lambda obj: obj.score, default=None)
        best_score = best_obj.score if best_obj is not None else None
        best_center_x = (
            best_obj.x + best_obj.w // 2
            if best_obj is not None
            else None
        )
        screen_img = draw_tuning_panel(img, best_score, best_center_x)
        dis.show(screen_img)
    # 运行阶段：不画任何东西，不 show 屏幕，只跑检测 + UART
