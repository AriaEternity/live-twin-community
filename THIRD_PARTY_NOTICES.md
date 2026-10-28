# 素材、数据与外部依赖来源

本仓库自有代码采用 [MIT](LICENSE) 许可。第三方角色、卡图、效果文本、公开数据快照、字体及外部依赖不因代码开源而获得新的许可；它们保留各自权利和上游条款。

## 卡片及角色

Live☆Twin / Evil★Twin、游戏王角色、卡名、效果文本和相关卡图的权利归KONAMI及相应权利方。本项目是非官方粉丝项目，没有官方关联或认可。

- `cards/`：19张主题卡的整图、小图和裁切图，共57份，来源为[YGOPRODeck API/图像服务](https://ygoprodeck.com/api-guide/)。维护入口 `scripts/mirror_card_images.sh` 保留来源URL。
- `img/md_kisikil.png`、`md_lilla.png`、`playmat.png`、`sunny_card.png`：来自Fandom Yu-Gi-Oh Wiki相应角色/卡片条目，完整原图URL保留在上述维护脚本。部分文件沿用.png扩展名，实际编码是WebP。
- `img/icons/`：由现有主题卡图裁切的图标，按卡图派生素材处理，不标为原创图标。
- README中的实际界面截图也包含这些第三方素材，不是新增原创卡图。

## 公开数据快照

快照抓取日2026-10-27；OCG规则生效日2026-10-01。

| 文件 | 来源与范围 |
| --- | --- |
| `data/cards_db.json` | [YGOPRODeck全量属性](https://db.ygoprodeck.com/api/v7/cardinfo.php)与[百鸽公开资料](https://ygocdb.com/api)，14,645条API记录，包含先行/临时编号，不等于已发售OCG数量 |
| `data/card_texts.json` | 百鸽公开原始文本14,255条；缺失内容不编造；官方简中sc_name与社区cn_name分别处理 |
| `data/banlist_ocg.json` | 百鸽CID机器表与[Konami完整名单](https://www.yugioh-card.com/japan/event/limitregulation/index.php?list=202610)逐项核验，202规则及172图号映射 |

获取资料的公共API不意味着放弃原内容权利；这些快照供本项目检索、规则校验和回归测试使用。部署后请遵守上游使用限制、合理缓存，定期核实官方规则。动态搜卡图片目前仍使用上游地址，公开部署应进一步实施合适的本地图片缓存，不能持续高频热链。

## 其他视觉资源

- `img/emotes/sample/`：由仓库内 `scripts/make_emote_placeholders.py` 使用Pillow绘制的几何星、心形和动态样例，不含用户聊天图片。
- `img/card_pre_release.png`：主题卡图缺失时的装饰占位，不表示真实卡片。
- `docs/images/cover.png`：使用内置imagegen生成的抽象装饰封面，不是真实界面，不含官方卡图；提示词与用途见 `docs/visual-assets.md`。

## 软件与字体

- Flask、Flask-SQLAlchemy、Flask-Login、gunicorn、Pillow：由requirements.txt安装，各自许可证随对应上游分发。
- [GSAP](https://gsap.com/) 3.12.5及ScrollTrigger/ScrollToPlugin：从jsDelivr加载，使用时应遵守其上游许可。
- Noto Sans SC、DotGothic16、M PLUS Rounded 1c：从字体CDN加载，版权与许可归各字体项目。本仓库没有重新打包字体文件。

历史部署的备案号、组织分享链接、私人联系方式、账号记录及服务器资料均不属于公开版内容。
