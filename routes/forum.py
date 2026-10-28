"""论坛：板块列表 / 帖子列表 / 帖子详情 / 发帖 / 回复 / 点赞 / 管理操作 / 图片上传
       / 个人空间 / 主题编辑 / 论坛搜索 / @提及通知 / 楼层引用 / 管理日志 / 板块排序 / 站内公告
       / 收藏帖子 / 关注用户"""
import os
import re
import uuid
from datetime import datetime, timezone
from functools import wraps
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, abort, current_app)
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload, aliased
from models import (db, Board, Thread, Post, Like, Attachment, Notification,
                    User, ModerationLog, Announcement, Favorite, Follow, Deck)
from security import rate_limit
from wordfilter import find_sensitive, log_hit

forum_bp = Blueprint('forum', __name__, url_prefix='/forum')

# 红线词拦截统一提示：绝不回显具体命中词
_REDLINE_MSG = '内容包含违规信息，无法发布，请修改后重试'


def _check_redline(kind, fields):
    """红线词硬拦截：依次对 fields（{字段名: 原文}）做敏感词子串匹配。
    命中则写日志（snippet 用命中字段的原文）并返回 True，未命中返回 False。
    """
    for text in fields.values():
        word = find_sensitive(text)
        if word is not None:
            log_hit(kind, word, text, current_user.username,
                    request.remote_addr or '')
            return True
    return False


def _post_page(post, per_page):
    """计算某条回复在主题中的分页页码（从 1 开始）"""
    idx = Post.query.filter(
        Post.thread_id == post.thread_id,
        Post.created_at < post.created_at
    ).count()
    return idx // per_page + 1

# 发帖/回复内容长度限制
MIN_TITLE_LEN = 2
MAX_TITLE_LEN = 120
MIN_CONTENT_LEN = 2
MAX_CONTENT_LEN = 10000

# @提及：匹配注册账号名（规则见 routes/auth.py _USERNAME_RE：2-24 位字母/数字/下划线）
_MENTION_RE = re.compile(r'@([A-Za-z0-9_]{2,24})')


def _notify_mentions(content, thread, post):
    """解析内容中的 @账号名，给被提及的已注册用户建 mention 通知。
    需在 post 已 flush（拿到 id）后调用；通知随调用方后续的 commit 一起落库。
    跳过本人；同一接收者对同一 post 已存在任何通知（reply/mention）则去重跳过。
    本项目为纯文本无 markdown，直接全文解析。
    """
    usernames = set(_MENTION_RE.findall(content or ''))
    if not usernames:
        return
    users = User.query.filter(User.username.in_(usernames)).all()
    for u in users:
        if u.id == current_user.id:
            continue   # @自己不通知
        # autoflush 保证能看到同 session 里待提交的 reply 通知，借此去重
        dup = Notification.query.filter_by(recipient_id=u.id,
                                           post_id=post.id).first()
        if dup:
            continue
        db.session.add(Notification(
            recipient_id=u.id,
            actor_id=current_user.id,
            thread_id=thread.id,
            post_id=post.id,
            kind='mention',
        ))


def _log_mod(action, target_type, target_id, detail=''):
    """写管理操作日志。必须在主操作 commit 之后调用：
    日志写库失败只回滚日志自身，绝不影响已完成的主操作。"""
    try:
        db.session.add(ModerationLog(
            admin_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=(detail or '')[:256],
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


# ── 图片附件保存 ────────────────────────────────────

def save_images(post):
    """从当前请求保存上传图片到 uploads/ 并登记附件。返回错误信息或 None"""
    files = [f for f in request.files.getlist('images') if f and f.filename]
    if not files:
        return None
    if len(files) > current_app.config['MAX_IMAGES_PER_POST']:
        return f"最多上传 {current_app.config['MAX_IMAGES_PER_POST']} 张图片"

    # 先整体校验扩展名，全部通过再落盘（避免半截保存）
    allowed = current_app.config['ALLOWED_IMAGE_EXT']
    for f in files:
        ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
        if ext not in allowed:
            return '仅支持 png / jpg / gif / webp 图片'

    for f in files:
        ext = f.filename.rsplit('.', 1)[-1].lower()
        filename = f'{uuid.uuid4().hex}.{ext}'   # 随机文件名，杜绝路径注入与覆盖
        f.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
        db.session.add(Attachment(post_id=post.id, filename=filename))
    return None


# ── 板块列表 ──────────────────────────────────────

@forum_bp.route('/')
def index():
    boards = Board.query.order_by(Board.sort_order).all()
    # 最新一条公告：论坛首页弹窗用；前端 localStorage 记录已知晓的公告 id，
    # 发布新公告（新 id）会再次弹窗
    announcement = Announcement.query.order_by(Announcement.id.desc()).first()
    return render_template('forum/index.html', boards=boards, announcement=announcement)


# ── 站内公告：历史列表 ────────────────────────────

@forum_bp.route('/announcements')
def announcements():
    all_ann = Announcement.query.order_by(Announcement.id.desc()).all()
    return render_template('forum/announcements.html', announcements=all_ann)


# ── 板块 → 帖子列表 ───────────────────────────────

@forum_bp.route('/board/<slug>')
def board(slug):
    board = Board.query.filter_by(slug=slug).first_or_404()
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('THREADS_PER_PAGE', 15)
    # 排序：active(默认,按最后回复时间) / new(发帖时间) / replies(回复数) / views(浏览数)
    # 置顶帖永远排最前（is_pinned 是 Thread 本表列，所有排序都先看它）
    sort = request.args.get('sort', 'active')
    if sort not in ('active', 'new', 'replies', 'views'):
        sort = 'active'

    q = Thread.query.filter_by(board_id=board.id)
    if sort == 'new':
        q = q.order_by(Thread.is_pinned.desc(), Thread.created_at.desc())
    elif sort == 'views':
        q = q.order_by(Thread.is_pinned.desc(), Thread.view_count.desc(),
                       Thread.created_at.desc())
    elif sort == 'replies':
        # 相关子查询按 thread 粒度计数（走 ix_posts_thread_id 索引），
        # 不再对 posts 全表 GROUP BY 后再 outerjoin
        cnt_sub = (db.select(db.func.count(Post.id))
                   .where(Post.thread_id == Thread.id)
                   .scalar_subquery())
        q = q.order_by(Thread.is_pinned.desc(),
                       cnt_sub.desc(),
                       Thread.created_at.desc())
    else:  # active：bump 制，updated_at 在发帖时取默认、回帖时显式刷新
        q = q.order_by(Thread.is_pinned.desc(),
                       Thread.updated_at.desc())
    threads = q.paginate(page=page, per_page=per_page)
    # 模板注意：分页链接需带上 sort 参数，翻页不丢排序状态
    return render_template('forum/board.html', board=board, threads=threads,
                           sort=sort)


# ── 个人空间 ──────────────────────────────────────

@forum_bp.route('/user/<int:user_id>')
def user_profile(user_id):
    """个人空间：资料 + TA 的主题 + TA 的回复（双列表独立分页）

    模板 forum/user_profile.html 上下文：
      user          — 目标用户（display_name/username/created_at/signature/avatar_url 自行判空）
      threads       — 主题分页对象（created_at desc，15/页，参数 page_t）
      posts         — 回复分页对象（created_at desc，15/页，参数 page_p；已排除各主题首帖）
      is_following  — 当前登录用户是否已关注 TA（未登录恒 False）
      follower_count / followee_count — TA 的粉丝数 / 关注数
      recent_decks  — TA 最近更新的卡组行（updated_at desc 最多 5 条，
                      与卡组广场同款 {deck, main_count, extra_count, side_count}）
      deck_count    — TA 的卡组总数
    """
    user = User.query.get_or_404(user_id)
    page_t = request.args.get('page_t', 1, type=int)
    page_p = request.args.get('page_p', 1, type=int)
    per_page = current_app.config.get('THREADS_PER_PAGE', 15)

    threads = (user.threads
               .order_by(Thread.created_at.desc())
               .paginate(page=page_t, per_page=per_page))

    # “回复”= 非主题首帖的 Post：首帖是每个 thread 里 id 最小（即最早）的那条。
    # 改用相关子查询：外层每行匹配同主题的最小 id，避免全表 GROUP BY 扫描。
    first_post = aliased(Post)
    first_sq = (select(func.min(first_post.id))
                .where(first_post.thread_id == Post.thread_id)
                .scalar_subquery())
    posts = (Post.query
             .filter(Post.user_id == user.id)
             .filter(Post.id != first_sq)
             .order_by(Post.created_at.desc())
             .paginate(page=page_p, per_page=per_page))

    # 关注数据：当前用户是否已关注 TA + TA 的粉丝/关注计数
    is_following = False
    if current_user.is_authenticated:
        is_following = Follow.query.filter_by(
            follower_id=current_user.id, followee_id=user.id).first() is not None
    follower_count = Follow.query.filter_by(followee_id=user.id).count()
    followee_count = Follow.query.filter_by(follower_id=user.id).count()

    # 卡组数据：TA 最近更新的 5 套卡组（广场同款行结构）+ 总数
    recent_decks = []
    for deck in (user.decks
                 .order_by(Deck.updated_at.desc())
                 .limit(5).all()):
        main_count, extra_count, side_count = deck.counts()
        recent_decks.append({'deck': deck, 'main_count': main_count,
                             'extra_count': extra_count, 'side_count': side_count})
    deck_count = user.decks.count()

    return render_template('forum/user_profile.html', user=user,
                           threads=threads, posts=posts,
                           is_following=is_following,
                           follower_count=follower_count,
                           followee_count=followee_count,
                           recent_decks=recent_decks,
                           deck_count=deck_count)


# ── 帖子详情 ──────────────────────────────────────

def _render_thread(t, page, reply_content=''):
    """渲染帖子详情页；回复校验失败重渲染时用 reply_content 保留已输入内容"""
    per_page = current_app.config.get('POSTS_PER_PAGE', 20)
    # selectinload 预取本页楼层的关系，收敛模板渲染的 N+1：
    # author/attachments 每层必访问；parent/quoted 为自引用关系，
    # 模板还访问它们的 author.display_name，故再套一层二级预取
    posts = (t.posts
             .options(
                 selectinload(Post.author),
                 selectinload(Post.attachments),
                 selectinload(Post.parent).selectinload(Post.author),
                 selectinload(Post.quoted).selectinload(Post.author),
             )
             .paginate(page=page, per_page=per_page))

    # 点赞数据改为本页楼层两条 IN 聚合查询，避免模板里每楼各发 SQL
    post_ids = [p.id for p in posts.items]
    like_counts = {}
    liked_post_ids = set()
    if post_ids:
        like_counts = {
            pid: cnt for pid, cnt in db.session.execute(
                select(Like.post_id, func.count(Like.id))
                .where(Like.post_id.in_(post_ids))
                .group_by(Like.post_id)
            ).all()
        }
        if current_user.is_authenticated:
            liked_post_ids = set(db.session.scalars(
                select(Like.post_id)
                .where(Like.post_id.in_(post_ids), Like.user_id == current_user.id)
            ).all())

    # 收藏数据：当前用户是否已收藏本帖（未登录恒 False）+ 全帖收藏计数
    is_fav = False
    if current_user.is_authenticated:
        is_fav = Favorite.query.filter_by(
            user_id=current_user.id, thread_id=t.id).first() is not None
    fav_count = t.favorites.count()

    # 首帖摘要与首图供 OG 分享标签使用
    og_desc = t.title
    if len(og_desc) > 40:
        og_desc = og_desc[:40] + '…'
    og_image = url_for('main.card_image', filename='cropped/82699999.jpg', _external=True)
    first_post = t.posts.order_by(Post.id).first()
    if first_post and first_post.attachments:
        og_image = url_for('main.uploaded_file',
                           filename=first_post.attachments[0].filename,
                           _external=True)

    return render_template('forum/thread.html', thread=t, posts=posts,
                           reply_content=reply_content,
                           like_counts=like_counts,
                           liked_post_ids=liked_post_ids,
                           is_fav=is_fav, fav_count=fav_count,
                           og_desc=og_desc, og_image=og_image)


@forum_bp.route('/thread/<int:thread_id>')
def thread(thread_id):
    t = Thread.query.get_or_404(thread_id)
    # 原子自增：UPDATE threads SET view_count = view_count + 1，
    # 避免读-改-写在多 worker 并发下丢计数
    t.view_count = Thread.view_count + 1
    db.session.commit()

    page = request.args.get('page', 1, type=int)
    return _render_thread(t, page)


# ── 发帖 ──────────────────────────────────────────

@forum_bp.route('/board/<slug>/new', methods=['GET', 'POST'])
@login_required
def new_thread(slug):
    board = Board.query.filter_by(slug=slug).first_or_404()

    if request.method == 'POST':
        # 发帖限速：每 IP 每小时 10 帖（防刷屏与磁盘 DoS）
        if rate_limit('new_thread', 10, 3600):
            abort(429)

        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()

        if not (MIN_TITLE_LEN <= len(title) <= MAX_TITLE_LEN):
            flash(f'标题须为 {MIN_TITLE_LEN}-{MAX_TITLE_LEN} 个字符', 'error')
            return render_template('forum/new_thread.html', board=board,
                                   title=title, content=content)
        if not (MIN_CONTENT_LEN <= len(content) <= MAX_CONTENT_LEN):
            flash(f'内容须为 {MIN_CONTENT_LEN}-{MAX_CONTENT_LEN} 个字符', 'error')
            return render_template('forum/new_thread.html', board=board,
                                   title=title, content=content)

        # 红线词硬拦截：标题与正文分别检查，命中记日志 + 回填原文，不回显命中词
        if _check_redline('thread', {'title': title, 'content': content}):
            flash(_REDLINE_MSG, 'error')
            return render_template('forum/new_thread.html', board=board,
                                   title=title, content=content)

        thread = Thread(
            title=title,
            content=content,
            board_id=board.id,
            user_id=current_user.id,
        )
        db.session.add(thread)
        # 首帖 = 楼主正文
        post = Post(
            content=content,
            thread=thread,
            user_id=current_user.id,
        )
        db.session.add(post)
        db.session.flush()   # 拿到 post.id 供附件关联
        err = save_images(post)
        if err:
            db.session.rollback()
            flash(err, 'error')
            return render_template('forum/new_thread.html', board=board,
                                   title=title, content=content)
        _notify_mentions(content, thread, post)   # @提及通知（随下方 commit 落库）
        db.session.commit()
        flash('发帖成功！', 'success')
        return redirect(url_for('forum.thread', thread_id=thread.id))

    return render_template('forum/new_thread.html', board=board)


# ── 主题编辑（仅主题可编辑，回复不提供编辑） ──────────────

@forum_bp.route('/thread/<int:thread_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_thread(thread_id):
    """编辑主题：仅作者本人或管理员；锁定帖仅管理员可编辑。
    更新 Thread.title + Thread.content + 首帖 Post.content，并把首帖 edited_at 置为当前 UTC。

    模板 forum/edit_thread.html 上下文：
      thread  — 被编辑的主题
      title   — 回填标题（GET 为现值，POST 校验失败为用户已输入值）
      content — 回填正文（同上）
    """
    t = Thread.query.get_or_404(thread_id)
    if t.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    if t.is_locked and not current_user.is_admin:
        flash('该帖子已被锁定，仅管理员可编辑', 'error')
        return redirect(url_for('forum.thread', thread_id=thread_id))

    first_post = t.posts.order_by(Post.created_at).first()

    if request.method == 'POST':
        # 编辑限速：独立桶每 IP 每小时 20 次（不与发帖/回复桶互相消耗）
        if rate_limit('edit_thread', 20, 3600):
            abort(429)

        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()

        if not (MIN_TITLE_LEN <= len(title) <= MAX_TITLE_LEN):
            flash(f'标题须为 {MIN_TITLE_LEN}-{MAX_TITLE_LEN} 个字符', 'error')
            return render_template('forum/edit_thread.html', thread=t,
                                   title=title, content=content)
        if not (MIN_CONTENT_LEN <= len(content) <= MAX_CONTENT_LEN):
            flash(f'内容须为 {MIN_CONTENT_LEN}-{MAX_CONTENT_LEN} 个字符', 'error')
            return render_template('forum/edit_thread.html', thread=t,
                                   title=title, content=content)

        # 红线词硬拦截：标题与正文分别检查，命中记日志 + 回填原文，不回显命中词
        if _check_redline('edit', {'title': title, 'content': content}):
            flash(_REDLINE_MSG, 'error')
            return render_template('forum/edit_thread.html', thread=t,
                                   title=title, content=content)

        t.title = title
        t.content = content   # 与首帖正文保持一致（搜索/摘要引用的是 Thread.content）
        if first_post is not None:
            first_post.content = content
            first_post.edited_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('帖子已更新', 'success')
        return redirect(url_for('forum.thread', thread_id=thread_id))

    return render_template('forum/edit_thread.html', thread=t, title=t.title,
                           content=first_post.content if first_post else t.content)


# ── 论坛搜索 ──────────────────────────────────────

@forum_bp.route('/search')
def search():
    """全文搜索：LIKE 匹配主题标题/正文及回复内容，命中主题去重，按最后回复时间 desc。

    模板 forum/search.html 上下文：
      q       — 搜索词
      results — 主题分页对象（15/页，参数 page）；每个 item 上额外挂了
                hit_thread(bool, 主题标题/正文命中) 与 hit_reply(bool, 回复命中)
    """
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        flash('搜索关键词至少 2 个字符', 'error')
        return redirect(url_for('forum.index'))
    # 搜索限速：独立桶每 IP 每分钟 30 次
    if rate_limit('forum_search', 30, 60):
        abort(429)

    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('THREADS_PER_PAGE', 15)
    like = f'%{q}%'

    hit_sq = (db.session.query(Post.thread_id.label('tid'))
              .filter(Post.content.like(like)).subquery())
    query = (Thread.query
             .filter(db.or_(Thread.title.like(like),
                            Thread.content.like(like),
                            Thread.id.in_(db.session.query(hit_sq.c.tid))))
             .order_by(Thread.updated_at.desc()))
    results = query.paginate(page=page, per_page=per_page)

    # 标注命中类型（供模板区分“主题命中/回复命中”徽标）
    for t in results.items:
        t.hit_thread = (q.lower() in t.title.lower()) or (q in t.content)
        t.hit_reply = (Post.query
                       .filter(Post.thread_id == t.id, Post.content.like(like))
                       .first() is not None)

    return render_template('forum/search.html', q=q, results=results)


# ── 回复 ──────────────────────────────────────────

@forum_bp.route('/thread/<int:thread_id>/reply', methods=['POST'])
@login_required
def reply(thread_id):
    t = Thread.query.get_or_404(thread_id)
    # 当前页码随表单带回：校验失败重渲染原页，不丢用户输入
    page = request.args.get('page', 1, type=int)

    if t.is_locked:
        flash('该帖子已被锁定，无法回复', 'error')
        return redirect(url_for('forum.thread', thread_id=thread_id, page=page))

    # 回复限速：每 IP 每小时 30 条
    if rate_limit('reply', 30, 3600):
        abort(429)

    content = request.form.get('content', '').strip()
    if not (MIN_CONTENT_LEN <= len(content) <= MAX_CONTENT_LEN):
        flash(f'回复须为 {MIN_CONTENT_LEN}-{MAX_CONTENT_LEN} 个字符', 'error')
        return _render_thread(t, page, reply_content=content)

    # 红线词硬拦截：命中记日志 + 回填原文，不回显命中词
    if _check_redline('reply', {'content': content}):
        flash(_REDLINE_MSG, 'error')
        return _render_thread(t, page, reply_content=content)

    # 可选：指定回复某一层楼；必须属于同一主题
    parent_id = request.form.get('parent_post_id', type=int)
    parent = None
    if parent_id:
        parent = Post.query.filter_by(id=parent_id, thread_id=t.id).first()
        if not parent:
            flash('要回复的楼层不存在', 'error')
            return _render_thread(t, page, reply_content=content)

    # 可选：引用某一层楼（quote_post_id）；必须属于同一主题，否则静默忽略置 None
    quote_id = request.form.get('quote_post_id', type=int)
    quoted = None
    if quote_id:
        quoted = Post.query.filter_by(id=quote_id, thread_id=t.id).first()

    post = Post(content=content, thread_id=t.id, user_id=current_user.id,
                parent_post_id=parent.id if parent else None,
                quote_post_id=quoted.id if quoted else None)
    t.updated_at = datetime.now(timezone.utc)
    db.session.add(post)
    db.session.flush()   # 拿到 post.id 供附件关联
    err = save_images(post)
    if err:
        db.session.rollback()
        flash(err, 'error')
        return _render_thread(t, page, reply_content=content)
    db.session.commit()

    # 决定通知对象：回复了某层楼则通知该层作者，否则通知楼主；自己回复自己不发
    recipient_id = parent.user_id if parent else t.user_id
    if recipient_id != current_user.id:
        db.session.add(Notification(
            recipient_id=recipient_id,
            actor_id=current_user.id,
            thread_id=t.id,
            post_id=post.id,
        ))
    _notify_mentions(content, t, post)   # @提及通知（内部按 recipient+post 去重）
    db.session.commit()

    flash('回复成功！', 'success')
    # 跳到新回复所在的最后一页并锚定该楼层，而不是回到第 1 页
    per_page = current_app.config.get('POSTS_PER_PAGE', 20)
    return redirect(url_for('forum.thread', thread_id=thread_id,
                            page=_post_page(post, per_page)) + f'#post-{post.id}')


# ── 管理员权限装饰器 ────────────────────────────────

def admin_required(f):
    """仅管理员可访问，否则 403"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


# ── 点赞（切换） ────────────────────────────────────

@forum_bp.route('/post/<int:post_id>/like', methods=['POST'])
@login_required
def like_post(post_id):
    post = Post.query.get_or_404(post_id)

    # 已赞则取消，未赞则新增
    like = Like.query.filter_by(user_id=current_user.id,
                                post_id=post.id).first()
    if like:
        db.session.delete(like)
    else:
        db.session.add(Like(user_id=current_user.id, post_id=post.id))
    try:
        db.session.commit()
    except IntegrityError:
        # 双击/并发下两个事务同时通过“未赞”检查：后提交者撞唯一约束，回滚按已赞处理即可
        db.session.rollback()

    # 回到来源页并定位到该楼层；无来源时回退到帖子页
    anchor = f'#post-{post.id}'
    if request.referrer:
        base = request.referrer.split('#')[0]
        return redirect(base + anchor)
    return redirect(url_for('forum.thread', thread_id=post.thread_id) + anchor)


# ── 收藏帖子（切换） ─────────────────────────────────

@forum_bp.route('/thread/<int:thread_id>/favorite', methods=['POST'])
@login_required
def favorite_thread(thread_id):
    t = Thread.query.get_or_404(thread_id)
    # 来源页码随请求带回，跳回帖子时停在原分页
    page = request.args.get('page', 1, type=int)

    # 已收藏则取消，未收藏则新增
    fav = Favorite.query.filter_by(user_id=current_user.id,
                                   thread_id=t.id).first()
    if fav:
        db.session.delete(fav)
        msg = '已取消收藏'
    else:
        db.session.add(Favorite(user_id=current_user.id, thread_id=t.id))
        msg = '已收藏 ★'
    try:
        db.session.commit()
        flash(msg, 'success')
    except IntegrityError:
        # 双击/并发下两个事务同时通过“未收藏”检查：后提交者撞唯一约束，回滚按已收藏处理
        db.session.rollback()

    return redirect(url_for('forum.thread', thread_id=t.id, page=page))


# ── 关注用户（切换） ─────────────────────────────────

@forum_bp.route('/user/<int:user_id>/follow', methods=['POST'])
@login_required
def follow_user(user_id):
    u = User.query.get_or_404(user_id)
    if u.id == current_user.id:
        flash('不能关注自己', 'error')
        return redirect(url_for('forum.user_profile', user_id=u.id))

    # 已关注则取消，未关注则新增
    rel = Follow.query.filter_by(follower_id=current_user.id,
                                 followee_id=u.id).first()
    if rel:
        db.session.delete(rel)
        msg = '已取消关注'
    else:
        db.session.add(Follow(follower_id=current_user.id, followee_id=u.id))
        msg = '已关注 TA ★'
    try:
        db.session.commit()
        flash(msg, 'success')
    except IntegrityError:
        # 双击/并发撞唯一约束：回滚按已关注处理
        db.session.rollback()

    return redirect(url_for('forum.user_profile', user_id=u.id))


# ── 我的收藏列表 ─────────────────────────────────────

@forum_bp.route('/favorites')
@login_required
def favorites():
    """当前用户的收藏列表（收藏时间倒序，15/页，参数 page）。

    模板 forum/favorites.html 上下文：
      favorites — Favorite 分页对象，item 是 Favorite（模板用 f.thread 取帖子）
    """
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('THREADS_PER_PAGE', 15)
    items = (Favorite.query
             .filter_by(user_id=current_user.id)
             # selectinload 预取 thread 与 thread.board，模板逐条访问不触发 N+1
             .options(selectinload(Favorite.thread).selectinload(Thread.board))
             .order_by(Favorite.created_at.desc())
             .paginate(page=page, per_page=per_page))
    # 防御：收藏指向的帖子已被删（正常由删 Thread 的 ORM 级联清掉，此处兜底跳过）
    items.items = [f for f in items.items if f.thread is not None]
    return render_template('forum/favorites.html', favorites=items)


# ── 关注的人的帖子 ────────────────────────────────────

@forum_bp.route('/following')
@login_required
def following():
    """当前用户关注的人的主题列表（bump 排序 updated_at 倒序，15/页，参数 page）。

    模板 forum/following.html 上下文：
      threads        — Thread 分页对象
      followee_count — 当前用户关注的人数（空列表时模板给引导文案）
    """
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('THREADS_PER_PAGE', 15)
    # 子查询取关注的人 id 集合，空集时 in_ 子查询自然匹配不到任何行
    followee_sq = select(Follow.followee_id).where(
        Follow.follower_id == current_user.id)
    threads = (Thread.query
               .filter(Thread.user_id.in_(followee_sq))
               .options(selectinload(Thread.board))
               .order_by(Thread.updated_at.desc())
               .paginate(page=page, per_page=per_page))
    followee_count = Follow.query.filter_by(follower_id=current_user.id).count()
    return render_template('forum/following.html', threads=threads,
                           followee_count=followee_count)


# ── 我关注的人 / 我的粉丝 ──────────────────────────────

def _follow_relation_sets():
    """一次查出与当前用户相关的所有关注对，在 Python 里拆成
    （我关注的人集合, 我的粉丝集合），防逐条判断互关时的 N+1"""
    me = current_user.id
    pairs = (db.session.query(Follow.follower_id, Follow.followee_id)
             .filter((Follow.follower_id == me) | (Follow.followee_id == me))
             .all())
    my_followees = {fe for fr, fe in pairs if fr == me}
    my_followers = {fr for fr, fe in pairs if fe == me}
    return my_followees, my_followers


@forum_bp.route('/followees')
@login_required
def followees():
    """我关注的人（关注时间倒序，20/页，参数 page）；只展示当前用户自己的列表。

    模板 forum/follows.html 上下文（followers() 同构）：
      kind    — 'followees'（我关注的人）/ 'followers'（我的粉丝）
      entries — dict 分页对象，每项 {user, since, mutual}；mutual=互相关注
    """
    page = request.args.get('page', 1, type=int)
    items = (Follow.query
             .filter_by(follower_id=current_user.id)
             # selectinload 预取被关注用户，模板逐条访问 display_name/avatar_url 不触发 N+1
             .options(selectinload(Follow.followee))
             .order_by(Follow.created_at.desc())
             .paginate(page=page, per_page=20))
    _, my_followers = _follow_relation_sets()
    items.items = [{'user': f.followee, 'since': f.created_at,
                    'mutual': f.followee_id in my_followers}
                   for f in items.items]
    return render_template('forum/follows.html', kind='followees', entries=items)


@forum_bp.route('/followers')
@login_required
def followers():
    """我的粉丝（被关注时间倒序，20/页，参数 page）；上下文同 followees()"""
    page = request.args.get('page', 1, type=int)
    items = (Follow.query
             .filter_by(followee_id=current_user.id)
             .options(selectinload(Follow.follower))
             .order_by(Follow.created_at.desc())
             .paginate(page=page, per_page=20))
    my_followees, _ = _follow_relation_sets()
    items.items = [{'user': f.follower, 'since': f.created_at,
                    'mutual': f.follower_id in my_followees}
                   for f in items.items]
    return render_template('forum/follows.html', kind='followers', entries=items)


# ── 消息通知 ────────────────────────────────────────

@forum_bp.app_context_processor
def inject_notification_count():
    """向所有模板注入未读通知数（顶栏铃铛用）；数据库异常时兜底为 0，保证错误页也能渲染"""
    try:
        if current_user.is_authenticated:
            count = Notification.query.filter_by(
                recipient_id=current_user.id, is_read=False
            ).count()
        else:
            count = 0
    except Exception:
        db.session.rollback()
        count = 0
    return {'unread_notification_count': count}


@forum_bp.route('/notifications')
@login_required
def notifications():
    """通知列表：点击跳到对应楼层"""
    page = request.args.get('page', 1, type=int)
    per_page = current_app.config.get('POSTS_PER_PAGE', 25)
    items = (Notification.query
             .filter_by(recipient_id=current_user.id)
             .order_by(Notification.created_at.desc())
             .paginate(page=page, per_page=per_page))
    post_per_page = current_app.config.get('POSTS_PER_PAGE', 20)
    valid = []
    stale = False
    for n in items.items:
        # 残留通知（帖子/楼层已被删但通知未清）：顺手删除并跳过，保证本页永不因此 500
        if n.post is None or n.thread is None:
            db.session.delete(n)
            stale = True
            continue
        n.page = _post_page(n.post, post_per_page)
        valid.append(n)
    if stale:
        db.session.commit()
    items.items = valid
    return render_template('forum/notifications.html', notifications=items)


@forum_bp.route('/notification/<int:nid>/go')
@login_required
def notification_go(nid):
    """单条通知入口：标记已读后 302 到对应帖子页 + 楼层锚点"""
    n = Notification.query.get_or_404(nid)
    # 归属校验：不是自己的通知当作不存在
    if n.recipient_id != current_user.id:
        abort(404)
    # 残留通知（内容已被删除）：顺手清理后回通知页
    if n.post is None or n.thread is None:
        db.session.delete(n)
        db.session.commit()
        flash('该通知对应的内容已被删除', 'info')
        return redirect(url_for('forum.notifications'))
    n.is_read = True
    db.session.commit()
    per_page = current_app.config.get('POSTS_PER_PAGE', 20)
    page = _post_page(n.post, per_page)
    return redirect(url_for('forum.thread', thread_id=n.thread_id, page=page)
                    + f'#post-{n.post_id}')


@forum_bp.route('/notifications/mark-read', methods=['POST'])
@login_required
def mark_notifications_read():
    """全部标为已读"""
    Notification.query.filter_by(
        recipient_id=current_user.id, is_read=False
    ).update({'is_read': True})
    db.session.commit()
    next_page = request.args.get('next') or url_for('forum.notifications')
    return redirect(next_page)


# ── 管理：置顶 / 锁定（切换） ───────────────────────

@forum_bp.route('/thread/<int:thread_id>/pin', methods=['POST'])
@admin_required
def pin_thread(thread_id):
    t = Thread.query.get_or_404(thread_id)
    t.is_pinned = not t.is_pinned
    db.session.commit()
    _log_mod('pin' if t.is_pinned else 'unpin', 'thread', t.id, t.title[:50])
    flash('已置顶' if t.is_pinned else '已取消置顶', 'success')
    return redirect(url_for('forum.thread', thread_id=thread_id))


@forum_bp.route('/thread/<int:thread_id>/lock', methods=['POST'])
@admin_required
def lock_thread(thread_id):
    t = Thread.query.get_or_404(thread_id)
    t.is_locked = not t.is_locked
    db.session.commit()
    _log_mod('lock' if t.is_locked else 'unlock', 'thread', t.id, t.title[:50])
    flash('已锁定' if t.is_locked else '已解锁', 'success')
    return redirect(url_for('forum.thread', thread_id=thread_id))


# ── 管理：删除帖子（整串） ──────────────────────────

@forum_bp.route('/thread/<int:thread_id>/delete', methods=['POST'])
@admin_required
def delete_thread(thread_id):
    t = Thread.query.get_or_404(thread_id)
    board_slug = t.board.slug
    detail = t.title[:50]   # 先取摘要再删，供管理日志留痕

    # 同步清理该帖相关的消息通知，避免通知页残留死链
    Notification.query.filter_by(thread_id=t.id).delete(synchronize_session=False)
    # 先删全部楼层（Like 随 Post 级联删除），再删帖子
    for post in t.posts.all():
        db.session.delete(post)
    db.session.delete(t)
    db.session.commit()
    _log_mod('delete_thread', 'thread', thread_id, detail)

    flash('帖子已删除', 'success')
    return redirect(url_for('forum.board', slug=board_slug))


# ── 删除单个回复（作者本人或管理员） ─────────────────

@forum_bp.route('/post/<int:post_id>/delete', methods=['POST'])
@login_required
def delete_post(post_id):
    post = Post.query.get_or_404(post_id)
    # 权限：只有楼层作者本人或管理员能删；普通用户删他人楼层 403
    if post.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    thread_id = post.thread_id
    detail = post.content[:50]   # 先取摘要再删，供管理日志留痕

    # 首帖是楼主正文，删它等于删整帖，拒绝
    first_post = post.thread.posts.order_by(Post.created_at).first()
    if first_post and first_post.id == post.id:
        flash('首帖不能单独删除，请删除整个帖子', 'error')
        return redirect(url_for('forum.thread', thread_id=thread_id))

    # 同步清理指向该楼层的消息通知
    Notification.query.filter_by(post_id=post.id).delete(synchronize_session=False)
    db.session.delete(post)
    db.session.commit()
    # 管理日志只记「管理员删他人楼层」的 Moderation 行为；用户删自己的楼层不算管理操作，不入日志
    if current_user.is_admin and post.user_id != current_user.id:
        _log_mod('delete_post', 'post', post_id, detail)
    flash('回复已删除', 'success')
    return redirect(url_for('forum.thread', thread_id=thread_id) + f'#post-{post_id}')
