# 夜航操作台 · NightVoyage Console

游戏半自动操作台。把需要反复做的界面判定、按键和角色循环放在一个窗口里，减少手动操作。

英文名：**NightVoyage Console**；GitHub 仓库：[YianBu/NightVoyage-Console](https://github.com/YianBu/NightVoyage-Console)。

目前有三栏：

- **灾厄**：行动抉择自动点“继续挑战”，打到设定等级后停手。
- **主控角色**：按共享角色表执行技能循环，只按键，不接管走位。
- **按键设置**：全局游戏热键，所有脚本共用。

## 启动

1. 双击 `启动.bat`。
2. 在顶部选择游戏窗口；没有列出时勾选“显示全部窗口”。
3. 窗口保持 16:9，不要最小化。

## 灾厄

手动进入灾厄模式，打到第一个行动抉择弹窗，然后设置目标等级并点“开始监视”。

- 等级按 5 递增：100、105、110；输入 108 会按 110 处理。
- 下一轮等级不超过目标时自动点“继续挑战”；到达目标后停止点击并响铃。
- 只认等级差为 5 的灾厄序列，常规扼守的 01、02、03 轮次弹窗会显示“已忽略”。
- 支持灾厄的两种弹窗高度：上移布局和常规布局都能按实际按钮位置点击。

## 主控角色

选择角色后点“开始主控”。角色表和执行逻辑沿用之前那套循环：

- 法露茜：锁定 + 连续终结技，之后普攻、定时战技和终结技。
- 黎瑟、黎瑟SP、伊薇/戏剧、止流/戏剧。
- “自定义”：按下方序列执行，格式为 `动作:等待秒数, 动作:等待秒数`，可用动作有战技、短按战技、长按战技、终结技、魔灵支援、普攻、重击、锁定。

带“迷津”的循环属于迷津副本的特化循环，不在主控角色页列出；数据表仍然保留，留给以后单独的迷津模块。

输入方式：

- **前台按键（推荐）**：游戏窗口在前台时才发送；切走自动暂停。
- **后台消息**：向窗口发送按键消息；适合支持后台输入的客户端。

“开始前先锁定目标”会在循环开始前按一次锁定键；角色起手里自己写了“锁定”则不重复。

黎瑟SP 的蓄力射击会读准星下方的百分比，满 100% 按普攻；前 3 发读百分比，之后固定 1.2 秒射击一次。

主控角色只负责按键，不负责走位、进本、切人和结算。建议在战斗中开始，离开战斗时手动停止。

主控角色可以和灾厄同时运行：灾厄检测到行动抉择弹窗时会自动暂停主控按键，点完继续、弹窗消失后再恢复。

## 按键设置

全局按键，所有模块共用，和游戏内设置保持一致：

- 交互、战技、终结技、魔灵支援、闪避、螺旋飞跃、普攻、射击、锁定。
- 键盘键写 `e`、`q`、`f`、`lshift`、`4`；鼠标键写 `鼠标左键`、`鼠标右键`、`鼠标中键`。
- “恢复默认”会把九项恢复成默认值。

脚本运行期间按键输入框会锁定，停止后可以继续修改。

## 更新

- 界面右下角有 `v0.0.1 · 检查更新`，点它会查询 GitHub 最新 Release；有新版会直接打开下载页。
- 用 `git clone` 拉下来的用户：双击 `更新.bat`，或在目录里执行 `git pull --ff-only`。
- 下载 zip 的用户：打开 [Releases](https://github.com/YianBu/NightVoyage-Console/releases)，下载最新 zip，覆盖解压到原目录；`profile.json`、`logs/`、`screenshots/` 不会被 Release 包覆盖。
- 发布新版：同步修改 `project_meta.py` 和 `pyproject.toml` 的版本号，推送后打 `v0.0.2` 这类 tag；仓库里的 GitHub Actions 会自动打包并生成 Release。

## 文件

- `main.py`：启动入口。
- `ui.py`：三栏界面与交互。
- `engine.py`：灾厄监视、判定、点击。
- `vision.py`：灾厄弹窗与等级识别区域。
- `character_rotation.py`：角色循环数据表。
- `character_mixin.py`：角色循环执行器。
- `character_runner.py`：把角色循环接到当前窗口、按键和 OCR。
- `input_keys.py`：键盘/鼠标按键解析与发送。
- `windowing.py`：窗口枚举、截图、后台/前台点击。
- `settings_store.py`：保存等级、角色和全局按键。
- `更新.bat`：git 克隆版拉取更新；zip 版打开 Releases 页面。
- `.github/workflows/release.yml`：推送 tag 时自动打包 Release。
- `logs/`：运行记录。
- `screenshots/`：识别测试截图。

## 环境

Windows 10/11、Python 3.13。依赖见 `requirements.txt`：

```bat
python -m pip install -r requirements.txt
```

本机已经装好这些依赖，直接运行 `启动.bat` 即可。

## English

**NightVoyage Console** ([YianBu/NightVoyage-Console](https://github.com/YianBu/NightVoyage-Console)) is a semi-auto control deck for Duet Night Abyss. It currently includes a calamity action-dialog helper, a main-character rotation runner, and global game key bindings. Character rotations are data-driven; the Mijin-specific rotations are kept out of the general main-character list.

