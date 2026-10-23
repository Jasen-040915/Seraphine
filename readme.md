# Seraphine · 免费开源版

英雄联盟 Windows 桌面工具，基于 Seraphine 1.1.4，增加装备经济显示、计分板跟随和本地语音输入。

**全部功能免费使用，无需付款、激活码、设备授权或账号登录本软件。** 购买页面、支付服务和功能授权检查已经移除。

## 功能

- 战绩查询、符文和原版客户端辅助功能。
- 装备经济：按住 Tab 显示团队合计与同行装备差值，跟随原计分板位置。
- 经济分享：主动点击差值发送到游戏聊天。
- 本地语音：Enter 打开聊天后说话，识别文字可编辑，由你按 Enter 发送；Esc 取消。
- 麦克风选择与测试、独立按键服务、托盘与单实例窗口恢复。

装备经济比较的是可见装备的商店总价，不是总收入或剩余金币。游戏需使用**无边框或窗口模式**。语音音频在本机处理。

## 下载与使用

在本仓库的 [Releases](https://github.com/Jasen-040915/Seraphine/releases) 下载完整 Windows 便携包，解压后运行 `Seraphine.exe`；请保留旁边的依赖和 `models` 目录。源码 ZIP 不能直接当作 Windows 程序运行。

使用方法见 [增强版说明](使用说明-增强版.md) 和 [便携版说明](document/便携版使用说明.md)。

## 从源码运行

需要 Windows 10/11 x64、Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-plus.txt
.\.venv\Scripts\python.exe scripts\download_voice_model.py
.\.venv\Scripts\python.exe main.py
```

模型下载脚本核对发布包 SHA256，并保留模型和语音运行库的许可文件。模型权重不进入 Git 仓库。

## 测试与打包

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\build-plus.ps1
```

构建结果位于 `dist/Seraphine`。构建和隐私排除规则见 [打包说明](document/便携版打包说明.md)。

## 来源与许可

基于 [Lionbilii/Seraphine](https://github.com/Lionbilii/Seraphine) 的 1.1.4 源码，上游项目为 [Zzaphkiel/Seraphine](https://github.com/Zzaphkiel/Seraphine)。保留原作者 Zzaphkiel 等贡献者的版权、原版说明和免责声明，见 [上游 README](document/UPSTREAM_README.md)。

本版程序源码按 [GNU GPL v3](LICENSE) 提供，允许依据许可证使用、研究、修改和再分发。修改记录见 [MODIFICATIONS.md](MODIFICATIONS.md)。第三方依赖、模型与游戏素材保留各自许可及权利声明。

本项目未经 Riot Games 或腾讯认可，与其没有隶属关系。游戏规则与反作弊行为可能变化；离线测试不代表已获游戏方批准，也不代表所有游戏环境都已验证。软件按许可证规定的现状提供。
