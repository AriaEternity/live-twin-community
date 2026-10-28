"""管理面板：仪表盘 / 用户管理 / 帖子管理 / 站内公告 / 管理操作日志（仅管理员）"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import current_user

from models import (db, User, Thread, Post, Like, Notification, ModerationLog,
                    Announcement, PrivateMessage, MessageAttachment,
                    Favorite, Follow, Deck)
from routes.forum import admin_required

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# 日志 action 白名单（与 models.ModerationLog 注释一致，论坛侧动作由 forum.py 写入）
LOG_ACTIONS = ('pin', 'unpin', 'lock', 'unlock',
               'delete_thread', 'delete_post',
               'grant_admin', 'revoke_admin', 'delete_user',
               'publish_announcement', 'delete_announcement')
LOGS_PER_PAGE = 20

# 公告长度限制
MAX_ANN_TITLE_LEN = 120
MAX_ANN_CONTENT_LEN = 5000


def log_action(action, target_type, target_id, detail=''):
    """写一条管理操作日志。日志失败不得影响主操作，异常全部吞掉并回滚日志事务"""
    try:
        db.session.add(ModerationLog(
            admin_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail[:256],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.warning('管理日志写入失败: %s %s#%s',
                                   action, target_type, target_id)


# ── 仪表盘 ─────────────────────────────────────────

@admin_bp.route('/')
@admin_required
def panel():
    stats = {
        'users': User.query.count(),
        'threads': Thread.query.count(),
        'posts': Post.query.count(),
        'likes': Like.query.count(),
    }
    recent = Thread.query.order_by(Thread.created_at.desc()).limit(8).all()
    return render_template('admin/panel.html', stats=stats, recent=recent)


# ── 用户管理 ───────────────────────────────────────

@admin_bp.route('/users')
@admin_required
def users():
    all_users = User.query.order_by(User.created_at).all()
    return render_template('admin/users.html', users=all_users)


@admin_bp.route('/user/<int:user_id>/toggle-admin', methods=['POST'])
@admin_required
def toggle_admin(user_id):
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        flash('不能取消自己的管理员权限', 'error')
        return redirect(url_for('admin.users'))
    if u.username == current_app.config['ADMIN_USERNAME']:
        flash('主管理员账号不可变更', 'error')
        return redirect(url_for('admin.users'))
    u.is_admin = not u.is_admin
    name = u.username
    db.session.commit()
    # 主操作成功后再写日志，日志失败不影响授权结果
    log_action('grant_admin' if u.is_admin else 'revoke_admin', 'user', u.id, name)
    flash(f'已{"授予" if u.is_admin else "取消"} {name} 的管理员权限', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/user/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        flash('不能删除自己的账号', 'error')
        return redirect(url_for('admin.users'))
    if u.username == current_app.config['ADMIN_USERNAME']:
        flash('主管理员账号不可删除', 'error')
        return redirect(url_for('admin.users'))

    # 1) 其主题连带全部楼层（含别人在他帖下的回复），同步清理相关通知
    his_threads = Thread.query.filter_by(user_id=u.id).all()
    thread_ids = [t.id for t in his_threads]
    if thread_ids:
        Notification.query.filter(Notification.thread_id.in_(thread_ids)) \
                          .delete(synchronize_session=False)
    for t in his_threads:
        for p in t.posts.all():
            db.session.delete(p)
        db.session.delete(t)
    # 2) 他在别人帖下的回复（ORM 级联清理这些回复的 Like/Attachment 行），同步清理相关通知
    his_posts = Post.query.filter_by(user_id=u.id).all()
    post_ids = [p.id for p in his_posts]
    if post_ids:
        Notification.query.filter(Notification.post_id.in_(post_ids)) \
                          .delete(synchronize_session=False)
    for p in his_posts:
        db.session.delete(p)
    # 3) 他点过的赞
    Like.query.filter_by(user_id=u.id).delete()
    # 4) 发给他的通知与他触发的通知
    Notification.query.filter(
        (Notification.recipient_id == u.id) | (Notification.actor_id == u.id)
    ).delete(synchronize_session=False)
    # 5) 他收发的私信（不清理会让收件人顶栏未读徽章永远清不掉，且留孤儿数据）
    msg_ids = [m.id for m in PrivateMessage.query.filter(
        (PrivateMessage.sender_id == u.id) | (PrivateMessage.recipient_id == u.id)
    ).all()]
    # bulk delete 不触发 ORM 级联，先删私信图片附件行再删消息本体；
    # 磁盘上的图片文件不删，与项目「uploads 只增不减」惯例一致
    if msg_ids:
        MessageAttachment.query.filter(MessageAttachment.message_id.in_(msg_ids)) \
                               .delete(synchronize_session=False)
        PrivateMessage.query.filter(PrivateMessage.id.in_(msg_ids)) \
                            .delete(synchronize_session=False)
    # 6) 他收藏过的帖子（他帖子上的收藏由上方删 Thread 的 ORM 级联处理）
    Favorite.query.filter_by(user_id=u.id).delete(synchronize_session=False)
    # 7) 他关注别人与别人关注他的关系（双向都清，避免粉丝页留死链）
    Follow.query.filter(
        (Follow.follower_id == u.id) | (Follow.followee_id == u.id)
    ).delete(synchronize_session=False)
    # 8) 他构筑的卡组（JSON 快照全在表内，无外部资源要清）
    Deck.query.filter_by(user_id=u.id).delete(synchronize_session=False)

    name = u.username
    db.session.delete(u)
    db.session.commit()
    # 全部级联清理 commit 成功后再写日志，避免日志事务打断清理流程
    log_action('delete_user', 'user', user_id, name)
    flash(f'用户 {name} 及其全部内容已删除', 'success')
    return redirect(url_for('admin.users'))


# ── 帖子管理 ───────────────────────────────────────

@admin_bp.route('/threads')
@admin_required
def threads():
    page = request.args.get('page', 1, type=int)
    pagination = (Thread.query
                  .order_by(Thread.is_pinned.desc(), Thread.created_at.desc())
                  .paginate(page=page, per_page=current_app.config.get('THREADS_PER_PAGE', 15)))
    return render_template('admin/threads.html', threads=pagination)


@admin_bp.route('/thread/<int:thread_id>/toggle-pin', methods=['POST'])
@admin_required
def toggle_pin(thread_id):
    t = Thread.query.get_or_404(thread_id)
    t.is_pinned = not t.is_pinned
    db.session.commit()
    flash('已置顶' if t.is_pinned else '已取消置顶', 'success')
    return redirect(url_for('admin.threads', page=request.args.get('page', 1)))


@admin_bp.route('/thread/<int:thread_id>/toggle-lock', methods=['POST'])
@admin_required
def toggle_lock(thread_id):
    t = Thread.query.get_or_404(thread_id)
    t.is_locked = not t.is_locked
    db.session.commit()
    flash('已锁定' if t.is_locked else '已解锁', 'success')
    return redirect(url_for('admin.threads', page=request.args.get('page', 1)))


@admin_bp.route('/thread/<int:thread_id>/delete', methods=['POST'])
@admin_required
def delete_thread(thread_id):
    t = Thread.query.get_or_404(thread_id)
    # 同步清理该帖相关的消息通知，避免通知页残留死链
    Notification.query.filter_by(thread_id=t.id).delete(synchronize_session=False)
    for p in t.posts.all():
        db.session.delete(p)
    db.session.delete(t)
    db.session.commit()
    flash('帖子已删除', 'success')
    return redirect(url_for('admin.threads', page=request.args.get('page', 1)))


# ── 站内公告 ─────────────────────────────────────

@admin_bp.route('/announcements', methods=['GET', 'POST'])
@admin_required
def announcements():
    """发布新公告 + 历史列表。发布后最新一条会在论坛首页向所有访客弹窗，
    用户点「我已知晓」后由前端 localStorage 记录该公告 id 不再弹。"""
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        if not title or not content:
            flash('公告标题和内容都不能为空', 'error')
            return redirect(url_for('admin.announcements'))
        if len(title) > MAX_ANN_TITLE_LEN or len(content) > MAX_ANN_CONTENT_LEN:
            flash(f'标题最长 {MAX_ANN_TITLE_LEN} 字，内容最长 {MAX_ANN_CONTENT_LEN} 字', 'error')
            return redirect(url_for('admin.announcements'))
        ann = Announcement(title=title, content=content)
        db.session.add(ann)
        db.session.commit()
        log_action('publish_announcement', 'announcement', ann.id, title)
        flash('公告已发布，将在论坛首页向所有访客弹窗', 'success')
        return redirect(url_for('admin.announcements'))
    all_ann = Announcement.query.order_by(Announcement.id.desc()).all()
    return render_template('admin/announcements.html', announcements=all_ann,
                           max_title=MAX_ANN_TITLE_LEN, max_content=MAX_ANN_CONTENT_LEN)


@admin_bp.route('/announcements/<int:ann_id>/delete', methods=['POST'])
@admin_required
def delete_announcement(ann_id):
    """删除一条公告。若删的是最新一条，首页弹窗会回退到上一条
    （用户 localStorage 里已 ack 的 id 不存在了，会重新看到上一条，属预期行为）。"""
    ann = Announcement.query.get_or_404(ann_id)
    title = ann.title
    db.session.delete(ann)
    db.session.commit()
    log_action('delete_announcement', 'announcement', ann_id, title)
    flash(f'公告「{title}」已删除', 'success')
    return redirect(url_for('admin.announcements'))


# ── 管理操作日志 ───────────────────────────────────

@admin_bp.route('/logs')
@admin_required
def logs():
    page = request.args.get('page', 1, type=int)
    # ?action= 精确筛选，非法值忽略
    action_filter = request.args.get('action', '')
    query = ModerationLog.query
    if action_filter in LOG_ACTIONS:
        query = query.filter_by(action=action_filter)
    else:
        action_filter = ''
    pagination = (query.order_by(ModerationLog.created_at.desc())
                  .paginate(page=page, per_page=LOGS_PER_PAGE))
    return render_template('admin/logs.html',
                           logs=pagination,
                           actions=LOG_ACTIONS,
                           current_action=action_filter)
