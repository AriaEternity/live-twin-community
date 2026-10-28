"""数据模型：用户 · 板块 · 帖子 · 回复 · 站内公告 · 搜卡缓存"""
import json
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


# ── 用户 ────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(24), unique=True, nullable=False, index=True)
    nickname = db.Column(db.String(24), default='')
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    avatar_url = db.Column(db.String(512), default='')
    signature = db.Column(db.String(200), default='')
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def display_name(self):
        """对外展示的名称：优先使用昵称，未设置则回退用户名"""
        return self.nickname or self.username

    # 关系
    threads = db.relationship('Thread', backref='author', lazy='dynamic')
    posts = db.relationship('Post', backref='author', lazy='dynamic')

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


# ── 板块 ────────────────────────────────────────────

class Board(db.Model):
    __tablename__ = 'boards'

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(32), unique=True, nullable=False)   # URL 友好名
    name = db.Column(db.String(64), nullable=False)                 # 显示名
    description = db.Column(db.String(256), default='')
    sort_order = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    threads = db.relationship('Thread', backref='board', lazy='dynamic')

    @property
    def thread_count(self):
        return self.threads.count()

    @property
    def last_post(self):
        return Post.query.join(Thread).filter(Thread.board_id == self.id) \
                          .order_by(Post.created_at.desc()).first()


# ── 帖子 ────────────────────────────────────────────

class Thread(db.Model):
    __tablename__ = 'threads'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    is_pinned = db.Column(db.Boolean, default=False)
    is_locked = db.Column(db.Boolean, default=False)
    view_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # bump 排序依据：发帖时取 default；回帖时由 routes/forum.py reply() 显式刷新。
    # 刻意不加 onupdate——view_count 自增、置顶/锁定等任何 UPDATE 都会触发 onupdate，
    # 会把「刚被看过/被管理操作过」的帖子顶到板块最前，破坏「最近回复排前」语义。
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    board_id = db.Column(db.Integer, db.ForeignKey('boards.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    posts = db.relationship('Post', backref='thread', lazy='dynamic',
                            order_by='Post.created_at')

    @property
    def reply_count(self):
        total = self.posts.count()
        return total - 1 if total > 0 else 0

    @property
    def last_post(self):
        return self.posts.order_by(Post.created_at.desc()).first()


# ── 回复 ────────────────────────────────────────────

class Post(db.Model):
    __tablename__ = 'posts'
    # 复合索引：帖子详情分页热路径 WHERE thread_id=? ORDER BY created_at
    __table_args__ = (db.Index('ix_posts_thread_created', 'thread_id', 'created_at'),)

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    edited_at = db.Column(db.DateTime, nullable=True)

    thread_id = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    parent_post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True, index=True)
    quote_post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=True, index=True)

    parent = db.relationship('Post', remote_side='Post.id', foreign_keys=[parent_post_id],
                             backref=db.backref('children', lazy='dynamic'))
    quoted = db.relationship('Post', remote_side='Post.id', foreign_keys=[quote_post_id])
    likes = db.relationship('Like', backref='post', lazy='dynamic',
                            cascade='all, delete-orphan')
    attachments = db.relationship('Attachment', backref='post',
                                  cascade='all, delete-orphan')

    @property
    def like_count(self):
        return self.likes.count()

    def is_liked_by(self, user):
        return (user.is_authenticated and
                self.likes.filter_by(user_id=user.id).first() is not None)


# ── 点赞 ────────────────────────────────────────────

class Like(db.Model):
    __tablename__ = 'likes'
    __table_args__ = (db.UniqueConstraint('user_id', 'post_id', name='uq_like_user_post'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ── 帖子收藏 ──────────────────────────────────────────

class Favorite(db.Model):
    """帖子收藏：user_id+thread_id 唯一；随 Thread 的 ORM 删除自动级联
    （backref favorites 带 cascade delete-orphan，与 Like 同款）"""
    __tablename__ = 'favorites'
    __table_args__ = (db.UniqueConstraint('user_id', 'thread_id', name='uq_favorite_user_thread'),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    thread_id = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    thread = db.relationship('Thread', backref=db.backref('favorites', lazy='dynamic',
                                                          cascade='all, delete-orphan'))


# ── 用户关注 ──────────────────────────────────────────

class Follow(db.Model):
    """用户关注：follower 关注 followee；follower_id+followee_id 唯一"""
    __tablename__ = 'follows'
    __table_args__ = (db.UniqueConstraint('follower_id', 'followee_id', name='uq_follow_pair'),)

    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    followee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # 同一模型两个指向 users 的外键，relationship 均显式 foreign_keys 消歧（踩坑 #27）
    follower = db.relationship('User', foreign_keys=[follower_id])
    followee = db.relationship('User', foreign_keys=[followee_id])


# ── 卡组构筑 ──────────────────────────────────────────

class Deck(db.Model):
    """卡组构筑：卡条目以 JSON 快照存三列（主/额外/副），详情页零外部 API 依赖。
    卡条目格式：{"id": int, "name": str, "types": str}，
    types 是搜卡 API 的中文卡种字符串（如「怪兽卡 效果」）。"""
    __tablename__ = 'decks'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    title = db.Column(db.String(60), nullable=False)
    description = db.Column(db.Text, default='')
    main_cards = db.Column(db.Text, nullable=False, default='[]')
    extra_cards = db.Column(db.Text, nullable=False, default='[]')
    side_cards = db.Column(db.Text, nullable=False, default='[]')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref=db.backref('decks', lazy='dynamic'))

    # author/author_id 别名：与 Thread/Post 的 author 命名惯例对齐，模板直接用
    @property
    def author(self):
        return self.user

    @property
    def author_id(self):
        return self.user_id

    @staticmethod
    def _parse(raw):
        """解析卡条目 JSON 列，异常/格式不符时返回空列表（数据损坏不炸页面）"""
        try:
            data = json.loads(raw or '[]')
        except (ValueError, TypeError):
            return []
        return data if isinstance(data, list) else []

    def main(self):
        return self._parse(self.main_cards)

    def extra(self):
        return self._parse(self.extra_cards)

    def side(self):
        return self._parse(self.side_cards)

    def counts(self):
        """返回 (主卡组, 额外卡组, 副卡组) 张数三元组"""
        return len(self.main()), len(self.extra()), len(self.side())


# ── 图片附件 ──────────────────────────────────────────

class Attachment(db.Model):
    """帖子图片附件，文件本体存 uploads/（随机文件名）"""
    __tablename__ = 'attachments'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(64), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ── 消息通知 ──────────────────────────────────────────

class Notification(db.Model):
    """用户消息通知（kind='reply' 回复通知，kind='mention' @提及通知）"""
    __tablename__ = 'notifications'

    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    thread_id = db.Column(db.Integer, db.ForeignKey('threads.id'), nullable=False)
    post_id = db.Column(db.Integer, db.ForeignKey('posts.id'), nullable=False)
    kind = db.Column(db.String(16), nullable=False, server_default='reply', default='reply')
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    recipient = db.relationship('User', foreign_keys=[recipient_id],
                                backref=db.backref('notifications', lazy='dynamic',
                                                   order_by='Notification.created_at.desc()'))
    actor = db.relationship('User', foreign_keys=[actor_id])
    thread = db.relationship('Thread')
    post = db.relationship('Post')


# ── 私信 ────────────────────────────────────────────

class PrivateMessage(db.Model):
    """站内私信：sender → recipient 单向投递；会话视图由 routes/messages.py 按
    双方用户成对聚合（sender_id/recipient_id 互换即同一会话）。
    本期最小实现：不支持删除，不发 Notification。"""
    __tablename__ = 'private_messages'

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # 同一模型两个指向 users 的外键，relationship 均显式 foreign_keys 消歧（踩坑 #27）
    sender = db.relationship('User', foreign_keys=[sender_id])
    recipient = db.relationship('User', foreign_keys=[recipient_id])
    # 私信图片附件（随私信删除级联清理行；磁盘文件只增不减）
    images = db.relationship('MessageAttachment', backref='message',
                             cascade='all, delete-orphan')


# ── 私信图片附件 ──────────────────────────────────────

class MessageAttachment(db.Model):
    """私信图片附件，文件本体存 uploads/（随机文件名），与帖子附件同一目录"""
    __tablename__ = 'message_attachments'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(64), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey('private_messages.id'),
                           nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ── 管理操作日志 ──────────────────────────────────────

class ModerationLog(db.Model):
    """管理操作日志：置顶/锁定/删帖/授权等管理员行为留痕"""
    __tablename__ = 'moderation_logs'

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    action = db.Column(db.String(32), nullable=False)       # pin/unpin/lock/unlock/delete_thread/delete_post/grant_admin/revoke_admin/delete_user/publish_announcement/delete_announcement
    target_type = db.Column(db.String(16), nullable=False)  # thread/post/user/announcement
    target_id = db.Column(db.Integer, nullable=True)
    detail = db.Column(db.String(256), default='')          # 人类可读摘要（帖子标题/被操作用户名等）
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    admin = db.relationship('User', foreign_keys=[admin_id])

# ── 站内公告 ──────────────────────────────────────

class Announcement(db.Model):
    """站内公告：最新一条会在论坛首页弹窗提醒；用户点「我已知晓」后由前端
    localStorage 记录该公告 id（ack:<id>），发布新公告（新 id）会再次弹窗。
    历史公告在 /forum/announcements 全部可查。"""
    __tablename__ = 'announcements'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ── 限速计数 ──────────────────────────────────────

class RateHit(db.Model):
    """限速滑动窗口计数：一行 = 一次命中（key = scope:IP，ts = 命中时刻 epoch 秒）。
    由 security.py rate_limit() 以原生 SQL 读写；落库后多 worker 共享、重启不清。"""
    __tablename__ = 'rate_hits'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(80), nullable=False, index=True)
    ts = db.Column(db.Float, nullable=False)


# ── 搜卡查询缓存 ────────────────────────────────────

class CardQueryCache(db.Model):
    """百鸽 API 按查询词缓存，TTL 见 routes/cards.py"""
    __tablename__ = 'card_query_cache'

    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column('query', db.String(64), unique=True, nullable=False, index=True)
    result_json = db.Column(db.Text, nullable=False)   # JSON 数组（已规范化的卡片）
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


# ── 初始化种子数据 ──────────────────────────────────

def seed_default_boards():
    """首次运行时创建默认板块"""
    if Board.query.first() is not None:
        return
    boards = [
        Board(slug='general', name='综合讨论', description='双子的日常、动画、设定讨论',
              sort_order=1),
        Board(slug='deck', name='卡组构筑', description='直播☆双子/邪恶★双子卡组研究',
              sort_order=2),
        Board(slug='fanart', name='同人创作', description='插画、小说、cosplay、二创分享',
              sort_order=3),
        Board(slug='news', name='新闻情报', description='新卡消息、比赛战报、周边资讯',
              sort_order=4),
        Board(slug='trade', name='交易交换', description='卡牌交易、交换、估价',
              sort_order=5),
    ]
    db.session.add_all(boards)
    db.session.commit()
