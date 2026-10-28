# 本地开发、配置与验证

公开仓库用于复现和展示程序，不携带真实用户资料或生产环境。首次启动创建的是本机自己的数据库与默认板块。

## 环境与启动

需要 Python 3.10+。建议使用独立虚拟环境；Node.js用于运行JavaScript测试，网站本身没有前端构建步骤。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
```

macOS / Linux：

```bash
source .venv/bin/activate
```

安装与启动：

```bash
python -m pip install -r requirements.txt
python app.py
```

打开 http://127.0.0.1:8080 。开发入口默认只监听本机，并允许开发模式的临时随机会话密钥。开发进程重启后，这种临时密钥可能使既有登录会话失效；需要稳定会话时应自行注入密钥，不将其提交到仓库。

应用模块导入会创建应用、目录、表与种子板块。导入入口做脚本或测试前，必须先设置独立数据库、上传目录等配置。

## 配置

程序读取**进程环境变量**，不会自动加载 `.env`。`.env`样例只用于说明配置项，复制文件本身不会使配置生效。

| 变量 | 作用 |
| --- | --- |
| `SECRET_KEY` | Flask会话签名；生产模式必须显式设置，不能使用示例字符串 |
| `FLASK_DEBUG` | 本地开发使用`1`；生产不要开启 |
| `DATABASE_URL` | SQLAlchemy数据库URL；未设置时使用本地instance下的SQLite |
| `UPLOAD_FOLDER` | 用户上传存储目录；测试应使用独占目录 |
| `SENSITIVE_WORDS_FILE` | 自行配置的文本过滤词表路径 |
| `TRUST_PROXY_HEADERS` | 默认关闭；仅在一个受控反向代理后使用`1` |

PowerShell设置变量示例：

```powershell
$env:SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
python app.py
```

macOS / Linux：

```bash
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python app.py
```

这里的命令只在当前shell会话生成并设置密钥。不要把真实值写进README、截图、Git历史或问题报告。

`TRUST_PROXY_HEADERS=1`意味着信任一层转发。只有在应用端口受网络限制、确实由受控代理访问时才开启，不能仅因客户端发送了`X-Forwarded-*`就信任它。

Gunicorn用于类Unix生产运行，不是Windows开发入口。生产还需要独立评估HTTPS、代理、文件权限、备份及运行进程设置，不能直接把本地调试服务器暴露为生产服务。

## 显式授予管理员

注册始终创建普通用户，没有默认管理员账号或口令。先在页面注册本地账号，然后在同一虚拟环境、同一数据库配置下执行CLI。

PowerShell：

```powershell
$env:FLASK_DEBUG = "1"
python -m flask --app app grant-admin your_registered_username
```

macOS / Linux：

```bash
FLASK_DEBUG=1 python -m flask --app app grant-admin your_registered_username
```

将`your_registered_username`替换为已经注册的用户名。这是操作者本地的显式授权，不应通过公开默认用户名自动触发。

维护者核验身份后可用同样的环境运行 `python -m flask --app app reset-password your_registered_username`，交互输入并确认新密码，命令不会打印新密码。生产操作必须注入自己的SECRET_KEY和数据库配置，不使用开发模式。

## 公开资料与本地业务资料

`data/cards_db.json`、`data/banlist_ocg.json`、`data/card_texts.json`是附日期的公开卡片快照。属性记录含先行/临时编号和其他格式，不能把记录数当作已发售OCG卡片数量。

数据库、上传、敏感词表、命中日志和自建联系目录属于使用者的本地业务资料，不应提交。群索引使用空数据或自行编写的合成样例，不从真实联系目录复制。

仓库跟踪空的 `data/groups.example.json`，实际 `data/groups.json` 已忽略；需要配置目录时可先复制该样例，再自行维护。未配置时页面使用空列表。

文本过滤的词表是一词一行，可使用`#`注释。未配置有效词表时过滤集合为空；仓库包含检测机制不等于已经提供适用于任何场景的生产词库。

## 数据维护

先生成候选，不直接覆盖正在运行的应用数据：

```bash
python -B -X utf8 scripts/refresh_card_data.py --cache-dir .cache/card-refresh/sources --output-dir .cache/card-refresh/candidate-data
```

脚本默认使用实际执行日期。公开源快照保存在指定缓存目录，候选中包括属性、真实效果文本和已核验禁限。上游、MD5、全量性或关联检查失败时，应先排查，不用手工编造记录填补差异。

候选生成成功后，可以从相同公开源中提取主题卡资料：

```bash
python -B -X utf8 scripts/fetch_cn_text.py --cache-dir .cache/card-refresh/sources --use-cache --output .cache/card-refresh/theme-cards.json
```

对比候选与当前快照、审核来源和日期，并先备份需要替换的文件。确认后才更新主题文本：

```bash
python -B -X utf8 scripts/update_cn_desc.py --input .cache/card-refresh/theme-cards.json
```

不要把旧缓存标为今天重新抓取。离线复验需要保留原抓取日期，并使用完整缓存；它不能证明远端今天没有变化。

刷新管线使用逐文件原子写入，不是整组事务。属性、规则、文本及对应读取代码需要作为兼容版本一起切换，避免运行中的请求读到混合版本。静态代码变化还要同步更新资源引用和SW缓存版本。

## 测试

### 离线语义回归

```bash
python -B -X utf8 -m unittest discover -s tests -p 'test_*.py' -v
```

覆盖公开版的数据更新、中文来源优先级、规则加载、缓存/回退、卡组输入边界及主题文本更新等契约。具体数量以当前源码与实际运行结果为准。

### JavaScript回归

```bash
node --test tests/test_deck_editor_rules.mjs
node --test tests/test_sw_contract.mjs
```

编辑器测试执行真实脚本，验证规则请求失败/重试、同卡跨区合计与保存失败保留内容。SW契约测试验证真实SW脚本的分支逻辑，未提供HTTP地址时不检查实际服务资源。

网站启动后增加HTTP资源检查：

```bash
node tests/test_sw_contract.mjs http://127.0.0.1:8080
```

这能检查壳资源与manifest图标是否存在，不能单独证明浏览器已激活SW或完成实际离线导航。

### 隔离业务冒烟

```bash
python -B -X utf8 scripts/run_isolated_smoke.py
```

只运行卡片筛选：

```bash
python -B -X utf8 scripts/run_isolated_smoke.py --case smoke_cards_filter.py
```

runner为论坛、私信、社交、卡组和筛选五个脚本分别准备独占源码副本、合成数据库、词表、上传与临时目录。部分搜卡/缺名解析会访问真实公开上游，网络不可用时结果与耗时可能不同。

**不要在业务目录直接运行旧`smoke_*.py`。** 它们可能建表、写库、删库或清理测试文件；仅更换数据库URL不一定能隔离其他文件路径。

### 浏览器验收

建议实际检查：

1. 首页启动、主题卡册总数与分类数、详情打开/关闭及键盘焦点。
2. 名称/编号检索、条件筛选、分页、效果文本和规则角标。
3. 卡组编辑器规则日期、禁止卡拦截、同卡不同图号跨区投入合计、请求失败后内容保留。
4. 手机两列布局、长文本、触控及减少动效偏好。
5. 应用壳资源更新，以及独立本地环境下的真实离线首页/离线说明页。

写验证记录时区分离线模拟、真实HTTP、真实浏览器和未执行项目。不要把VM通过等同于浏览器已安装PWA，也不要为验证而在真实业务环境创建测试账号或清理站点资料。

## 已知边界

卡组工具不是完整赛事裁判；保留未知正整数先行卡兼容，不全面验证卡片存在性、卡种分区与发售状态。动态搜卡图片可能依赖上游，文字快照并不等于全卡图离线。PWA只保证其缓存策略覆盖的资源，社区动态内容和外部字体/动画资源仍受网络影响。

暂未指定代码开源许可证；第三方素材和数据授权边界见 [第三方素材说明](../THIRD_PARTY_NOTICES.md)。
