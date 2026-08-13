"""
web_ui.py — Flask HTTP 服务，提供 MJPEG 播放页面 + 录制

可修改参数：
  - start_flask() 的 port: HTTP 服务端口号（默认8080）
  - 录制按钮样式: 搜 .btn 可改颜色、大小、位置
"""

from flask import Flask, Response
import time

flask_app = Flask(__name__)

# 由 main.py 的跳帧推流逻辑写入最新 JPEG 数据
latest_jpeg = None


@flask_app.route("/stream")
def stream():
    """MJPEG 流端点，浏览器 <img> 标签可直接播放。"""
    def generate():
        while True:
            if latest_jpeg is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n'
                       + latest_jpeg + b'\r\n')
            time.sleep(0.05)
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')


@flask_app.route("/")
def index():
    return '''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        *{margin:0;padding:0;}
        body{background:#000;height:100dvh;overflow:hidden;}
        #stream{width:100%;height:100%;object-fit:contain;position:absolute;top:0;left:0;z-index:1;}
        #canvas{display:none;}
        .btn{
            position:fixed;bottom:40px;right:30px;
            width:80px;height:80px;border-radius:50%;
            background:#FFD700;border:none;
            font-size:20px;font-weight:bold;color:#333;
            box-shadow:0 4px 16px rgba(0,0,0,0.6);
            z-index:999;cursor:pointer;
            -webkit-tap-highlight-color:transparent;
        }
        .btn:active{transform:scale(0.95);}
        .btn.recording{background:#FF4444;color:#fff;}
    </style>
</head>
<body>
    <img id="stream" src="/stream" crossorigin="anonymous">
    <canvas id="canvas" style="display:none;"></canvas>
    <button id="btn" class="btn">录制</button>

    <script>
    // ===== 录制 =====
    // 录像由浏览器本地完成，停止后下载到手机或电脑，不写入 MaixCAM 存储。
    const img=document.getElementById('stream');
    const canvas=document.getElementById('canvas');
    const ctx=canvas.getContext('2d');
    const btn=document.getElementById('btn');
    let recording=false,mediaRecorder=null,chunks=[];

    // 定时把 img 内容复制到 canvas，用于录像（img 无法直接录制）
    setInterval(()=>{
        if(!img.naturalWidth)return;
        canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;
        ctx.drawImage(img,0,0);
    },100);

    btn.onclick=()=>{
        if(!recording){
            chunks=[];
            mediaRecorder=new MediaRecorder(canvas.captureStream(10),{mimeType:'video/webm'});
            mediaRecorder.ondataavailable=e=>chunks.push(e.data);
            mediaRecorder.onstop=()=>{
                const blob=new Blob(chunks,{type:'video/webm'});
                const url=URL.createObjectURL(blob);
                const a=document.createElement('a');
                a.href=url;a.download='record_'+Date.now()+'.webm';a.click();
                URL.revokeObjectURL(url);
            };
            mediaRecorder.start();
            btn.textContent='暂停';btn.classList.add('recording');recording=true;
        }else{
            mediaRecorder.stop();
            btn.textContent='录制';btn.classList.remove('recording');recording=false;
        }
    };
    </script>
</body>
</html>'''


def start_flask(port=8080):
    flask_app.run(host="0.0.0.0", port=port, debug=False)
