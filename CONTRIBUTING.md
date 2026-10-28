# 贡献指南

欢迎通过 Issue 和 Pull Request 改进 Live☆Twin Community。

## 问题反馈

请提供运行系统、Python 版本、相关页面、复现步骤、预期与实际结果；界面问题可以附上截图。涉及卡片时请附卡片编号、资料来源与快照日期；涉及禁限时请说明规则生效日。

使用合成账号、卡组和帖子复现问题。日志、截图与配置片段须移除 Cookie、密钥、联系方式及私人资料。可能涉及凭据或账号安全的问题，请按 [安全说明](SECURITY.md)处理。

## 本地开发

按照 [本地开发与配置](docs/local-development.md)创建虚拟环境、安装依赖并启动。测试使用独立数据库、临时上传目录和合成资料，不连接真实业务实例。

修改 Python 业务逻辑后运行离线回归：

```bash
python -B -X utf8 -m unittest discover -s tests -p 'test_*.py' -v
```

修改卡组交互或应用壳时，运行对应的 JavaScript 检查：

```bash
node --test tests/test_deck_editor_rules.mjs
node --test tests/test_sw_contract.mjs
```

需要覆盖论坛、私信、社交、卡组和筛选流程时，使用隔离运行器：

```bash
python -B -X utf8 scripts/run_isolated_smoke.py
```

不要在含业务数据的目录直接运行旧 `smoke_*.py`。各项检查是否需要上游网络，以及浏览器检查的覆盖范围，见 [验证记录](docs/validation.md)。

## 提交修改

- 一次提交集中解决一个问题，说明触发条件、改后行为和实际运行的检查。
- 说明、注释和提交信息使用简体中文；文本采用 UTF-8，无 BOM。
- 界面改动附真实运行截图，检查桌面、移动布局和控制台。
- 卡片资料更新保留来源、抓取日期及规则生效日，先生成候选再核验，不手工编造缺失的卡名或效果。
- 不提交运行数据库、上传、真实词表、凭据、群目录、私有部署信息或测试产物。
- 新增依赖和媒体资源注明来源与许可，保留第三方声明。

提交前运行公开文件检查：

```bash
python -B -X utf8 scripts/check_public_release.py
```

自有代码贡献遵循 [MIT](LICENSE)；第三方内容按 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)保留其原有权利。
