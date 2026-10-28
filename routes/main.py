"""首页 —— 保留现有沉浸式纯前端体验"""
from flask import Blueprint, current_app, send_from_directory
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    """根路径直接返回沉浸式首页（不动原有 HTML/CSS/JS）"""
    return send_from_directory(BASE, 'index.html')


@main_bp.route('/index.html')
def index_html():
    """直链 /index.html 与根路径同页（PWA 应用壳预缓存列表需要）"""
    return send_from_directory(BASE, 'index.html')


@main_bp.route('/css/<path:filename>')
def css(filename):
    return send_from_directory(os.path.join(BASE, 'css'), filename)


@main_bp.route('/js/<path:filename>')
def js(filename):
    return send_from_directory(os.path.join(BASE, 'js'), filename)


# 只声明公开静态资源路由；不暴露源码、测试产物或本地数据库目录。


@main_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """用户上传的图片附件"""
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@main_bp.route('/cards/<path:filename>')
def card_image(filename):
    """本地镜像的卡图（整图 / small/ / cropped/）；与 routes/cards.py 的 /cards 蓝图不冲突"""
    return send_from_directory(os.path.join(BASE, 'cards'), filename)


@main_bp.route('/img/<path:filename>')
def img_asset(filename):
    """本地镜像的立绘等素材（fandom 来源）；目录可能不存在，404 即可"""
    return send_from_directory(os.path.join(BASE, 'img'), filename)


@main_bp.route('/sw.js')
def service_worker():
    """PWA Service Worker：必须在根路径（作用域才覆盖全站）；no-cache 保证浏览器每次都检查更新"""
    resp = send_from_directory(BASE, 'sw.js')
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@main_bp.route('/manifest.json')
def pwa_manifest():
    """PWA 安装清单"""
    resp = send_from_directory(BASE, 'manifest.json')
    resp.mimetype = 'application/manifest+json'
    return resp


@main_bp.route('/offline.html')
def offline_page():
    """PWA 离线回退页（SW 导航请求 network-first 失败时返回）"""
    return send_from_directory(BASE, 'offline.html')


@main_bp.route('/install.html')
def install_page():
    """PWA 安装说明页（分平台引导；支持 beforeinstallprompt 的浏览器直接一键装）"""
    return send_from_directory(BASE, 'install.html')
