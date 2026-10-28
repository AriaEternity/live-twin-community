"""站内私信：会话列表 / 对话页（发消息 + 图片附件）/ 未读私信数注入"""
import os
import uuid
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, current_app)
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy import or_, and_, case, func
from sqlalchemy.orm import selectinload
from models import db, User, PrivateMessage, MessageAttachment
from security import rate_limit
from wordfilter import find_sensitive, log_hit

messages_bp = Blueprint('messages', __name__, url_prefix='/messages')

# 红线词拦截统一提示：绝不回显具体命中词
_REDLINE_MSG = '内容包含违规信息，无法发布，请修改后重试'

# 私信内容长度限制（允许空文字只发图，仅约束上限）
MAX_CONTENT_LEN = 2000

# 对话页最多展示最近多少条消息（本期最小实现：不做分页）
CONVERSATION_LIMIT = 200


def _check_redline(kind, fields):
    """红线词硬拦截：依次对 fields（{字段名: 原文}）做敏感词子串匹配。
    命中则写日志（snippet 用命中字段的原文）并返回 True，未命中返回 False。
    与 routes/forum.py 的同名函数保持一致语义。
    """
    for text in fields.values():
        word = find_sensitive(text)
        if word is not None:
            log_hit(kind, word, text, current_user.username,
                    request.remote_addr or '')
            return True
    return False


# ── 会话列表 ─────────────────────────────────────────

@messages_bp.route('/')
@login_required
def inbox():
    """当前用户的会话列表：SQL 聚合实现，每项含对方 User、最后一条消息、
    对方发来的未读数；按最后消息时间倒序。对方已删号的会话直接跳过。

    动机：旧实现把当前用户「全部」私信 .all() 进内存分组，单用户量大后有 OOM 风险，
    且每会话一次 User.query.get 是 N+1。改为 SQL 聚合 + 批量取回后，
    内存占用只与会话数挂钩（有界），查询固定为 4 条（最新 id / 未读数 / 消息 / 用户）。"""
    uid = current_user.id

    # 对方 id 列：我发出的消息取收件人，发给我的取发件人
    other_col = case((PrivateMessage.sender_id == uid, PrivateMessage.recipient_id),
                     else_=PrivateMessage.sender_id)

    # (1) 按会话分组取每个会话最新一条消息 id（id 自增即时间序）
    latest_rows = (db.session.query(other_col.label('other_id'),
                                    func.max(PrivateMessage.id).label('last_id'))
                   .filter(or_(PrivateMessage.sender_id == uid,
                               PrivateMessage.recipient_id == uid))
                   .group_by(other_col)
                   .all())
    if not latest_rows:
        return render_template('messages/inbox.html', conversations=[])

    # (2) 各会话对方发来的未读数（只数 recipient=我 且未读的）
    unread = dict(
        db.session.query(PrivateMessage.sender_id, func.count())
        .filter(PrivateMessage.recipient_id == uid,
                PrivateMessage.is_read == False)  # noqa: E712 SQLAlchemy 需用 == 生成表达式
        .group_by(PrivateMessage.sender_id)
        .all())

    # (3) 批量取回各会话最后一条消息（selectinload 预加载图片附件，inbox 模板判 [图片] 要用）
    last_msgs = (PrivateMessage.query
                 .filter(PrivateMessage.id.in_([r.last_id for r in latest_rows]))
                 .options(selectinload(PrivateMessage.images))
                 .all())
    last_by_id = {m.id: m for m in last_msgs}

    # (4) 一次性取回所有对方用户转 dict，消除逐会话 User.query.get 的 N+1
    other_ids = [r.other_id for r in latest_rows]
    users = {u.id: u for u in User.query.filter(User.id.in_(other_ids)).all()}

    conversations = []
    for r in latest_rows:
        other = users.get(r.other_id)
        if other is None:
            continue   # 对方账号已删除，跳过该会话
        conversations.append({
            'other': other,
            'last': last_by_id[r.last_id],
            'unread': unread.get(r.other_id, 0),
        })
    # 按最后消息时间倒序
    conversations.sort(key=lambda c: c['last'].created_at, reverse=True)

    return render_template('messages/inbox.html', conversations=conversations)


# ── 对话页（查看 + 发送）─────────────────────────────────

@messages_bp.route('/<int:user_id>', methods=['GET', 'POST'])
@login_required
def conversation(user_id):
    """与指定用户的对话：GET 展示最近 200 条并把对方发来的未读标记已读；
    POST 发送新私信（文字 / 图片至少其一，长度校验 / 红线词拦截 / 限速 / 不能发给自己）。"""
    if user_id == current_user.id:
        flash('不能给自己发私信', 'error')
        return redirect(url_for('messages.inbox'))

    other = User.query.get_or_404(user_id)
    uid = current_user.id

    if request.method == 'POST':
        # 私信限速：每 IP 每小时 30 条
        if rate_limit('message', 30, 3600):
            abort(429)

        content = request.form.get('content', '').strip()
        # 过滤掉无文件名的空项（多选文件框不上传时会带一个空字段）
        files = [f for f in request.files.getlist('images') if f and f.filename]

        # 文字与图片至少其一，允许空文字只发图
        if not content and not files:
            flash('说点什么或发张图吧', 'error')
            return redirect(url_for('messages.conversation', user_id=user_id))
        if len(content) > MAX_CONTENT_LEN:
            flash(f'私信最长 {MAX_CONTENT_LEN} 个字符', 'error')
            return redirect(url_for('messages.conversation', user_id=user_id))
        if len(files) > current_app.config['MAX_IMAGES_PER_POST']:
            flash(f"最多上传 {current_app.config['MAX_IMAGES_PER_POST']} 张图片", 'error')
            return redirect(url_for('messages.conversation', user_id=user_id))

        # 先整体校验扩展名，全部通过再落盘（避免半截保存），与 forum.save_images 一致
        allowed = current_app.config['ALLOWED_IMAGE_EXT']
        for f in files:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext not in allowed:
                flash('仅支持 png / jpg / gif / webp 图片', 'error')
                return redirect(url_for('messages.conversation', user_id=user_id))

        # 红线词硬拦截：仅在文字非空时检查，命中记日志，不回显命中词
        if content and _check_redline('message', {'content': content}):
            flash(_REDLINE_MSG, 'error')
            return redirect(url_for('messages.conversation', user_id=user_id))

        msg = PrivateMessage(sender_id=uid, recipient_id=other.id,
                             content=content)
        db.session.add(msg)
        # flush 先拿 msg.id，附件行外键要用
        db.session.flush()
        # 随机文件名落盘 uploads/（与帖子附件同目录），并登记附件行
        for f in files:
            ext = f.filename.rsplit('.', 1)[-1].lower()
            filename = f'{uuid.uuid4().hex}.{ext}'   # 随机文件名，杜绝路径注入与覆盖
            f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            db.session.add(MessageAttachment(message_id=msg.id, filename=filename))
        # 先查后插并发兜底：唯一约束冲突时回滚而非 500（踩坑 #23）
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('发送失败，请稍后重试', 'error')
            return redirect(url_for('messages.conversation', user_id=user_id))

        flash('已发送', 'success')
        return redirect(url_for('messages.conversation', user_id=user_id))

    # GET：双方消息按时间升序，最多最近 200 条
    pair = or_(
        and_(PrivateMessage.sender_id == uid,
             PrivateMessage.recipient_id == other.id),
        and_(PrivateMessage.sender_id == other.id,
             PrivateMessage.recipient_id == uid),
    )
    msgs = (PrivateMessage.query
            .options(selectinload(PrivateMessage.images))   # 预加载图片附件，避免 200 条消息 N+1
            .filter(pair)
            .order_by(PrivateMessage.created_at.desc())
            .limit(CONVERSATION_LIMIT)
            .all())
    msgs.reverse()   # 取最新 200 条后翻回升序

    # 把「对方发给自己」的未读消息标记为已读
    unread_msgs = [m for m in PrivateMessage.query.filter(
        PrivateMessage.sender_id == other.id,
        PrivateMessage.recipient_id == uid,
        PrivateMessage.is_read == False,  # noqa: E712 SQLAlchemy 需用 == 生成表达式
    ).all()]
    if unread_msgs:
        for m in unread_msgs:
            m.is_read = True
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()

    return render_template('messages/conversation.html',
                           other=other, messages=msgs)


# ── 未读私信数注入 ──────────────────────────────────────

@messages_bp.app_context_processor
def inject_unread_message_count():
    """向所有模板注入未读私信数（顶栏私信徽章用）；数据库异常时兜底为 0，保证错误页也能渲染"""
    try:
        if current_user.is_authenticated:
            count = PrivateMessage.query.filter_by(
                recipient_id=current_user.id, is_read=False
            ).count()
        else:
            count = 0
    except Exception:
        db.session.rollback()
        count = 0
    return {'unread_message_count': count}
